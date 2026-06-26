"""Scheduled signal generation (Section 13.6, 20.1).

Celery Beat fires `scan_all_signals` on a recurring cadence. For each tracked
symbol × configured timeframe it fetches candles (Hyperliquid REST), computes
indicators server-side, and evaluates each active strategy via the LLM engine,
writing a Signal row whenever a qualifying setup is found.

`run_scan()` is plain Python so it can be driven synchronously by the
run_signal_scan management command for testing without a worker.
"""

import logging

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.market_data.feeds import get_candles, get_candles_since
from apps.market_data.forex import market_open as forex_market_open
from apps.market_data.models import Symbol
from apps.watchlists.models import WatchlistItem

from .engine import SignalEngineError, generate_signal
from .evaluate import outcome_label, walk
from .indicators import compute_indicators
from .models import Signal, SignalService, TelegramDelivery, UserSignalSubscription
from .pregate import candidate_direction
from .quota import signal_quota_for

logger = logging.getLogger("signals.tasks")

MIN_CANDLES = 210  # need enough history for the 200 EMA (+ a small seeding buffer)

# Each signal timeframe is checked against the next higher one for trend
# agreement (regime filter). Frames absent here skip the alignment check, but
# ADX strength still applies.
_HTF_MAP = {
    "1m": "15m", "3m": "15m", "5m": "1h", "15m": "1h",
    "30m": "4h", "1h": "4h", "2h": "1d", "4h": "1d",
}


def _htf_direction(sym, htf: str, cache: dict) -> str | None:
    """Higher-timeframe trend bias on the last closed candle: 'BUY' (up),
    'SELL' (down), None (choppy), or 'ERR' if candles couldn't be fetched."""
    key = (sym.id, htf)
    if key in cache:
        return cache[key]
    direction = "ERR"
    try:
        candles = get_candles(sym, htf, limit=300)
        if len(candles) >= MIN_CANDLES:
            ind = compute_indicators(candles)
            close, ema200 = ind["close"], ind["ema200"]
            if None not in (close, ema200):
                # Bias off the 200 EMA alone: above = bullish, below = bearish.
                # Only blocks genuinely counter-trend signals — not every time the
                # higher timeframe's fast EMAs are momentarily mixed.
                direction = "BUY" if close > ema200 else "SELL"
    except (requests.RequestException, ValueError):
        direction = "ERR"
    cache[key] = direction
    return direction


def _regime_ok(sym, tf: str, direction: str, indicators: dict, htf_cache: dict) -> bool:
    """True if the market is trending (ADX) and the higher timeframe agrees with
    `direction`. Fails open on a higher-timeframe fetch error so a transient API
    hiccup doesn't silence the whole feed."""
    adx = indicators.get("adx")
    if adx is None or adx < settings.SIGNAL_ADX_MIN:
        return False
    htf = _HTF_MAP.get(tf)
    if not htf:
        return True  # no higher frame configured — ADX strength alone
    htf_dir = _htf_direction(sym, htf, htf_cache)
    if htf_dir == "ERR":
        return True
    if htf_dir is None:
        return False  # higher timeframe is choppy → skip
    return direction == htf_dir


def run_scan(symbol_limit: int | None = None, use_pregate: bool | None = None) -> dict:
    """Evaluate active strategies across symbols/timeframes. Returns a summary
    including LLM call/token stats for cost visibility."""
    services = list(SignalService.objects.filter(is_active=True))
    if not services:
        return {"created": 0, "note": "no active services"}

    # Only scan coins someone actually watches (union of all watchlists). If no
    # one has a watchlist, there's nothing to scan — don't generate signals for
    # coins nobody is watching (and don't spend LLM on them).
    watched_ids = list(WatchlistItem.objects.values_list("symbol_id", flat=True).distinct())
    if not watched_ids:
        return {"created": 0, "note": "no watched symbols — nothing to scan"}
    symbols = Symbol.objects.filter(is_active=True, id__in=watched_ids)
    limit = settings.SIGNAL_SCAN_SYMBOL_LIMIT if symbol_limit is None else symbol_limit
    if limit:
        symbols = symbols[:limit]

    # Dedup: while a strategy has an open (PENDING) call on a symbol+timeframe,
    # don't issue another in the *same* direction — one live call per strategy per
    # symbol per timeframe until it hits SL/TP. A fresh call is only allowed when
    # the trend flips (the cheap directional bias points the opposite way), in
    # which case the stale opposite call is invalidated. Keying by timeframe is
    # essential: a 1h and a 4h call are different trades, so a flip on one frame
    # must not invalidate — or re-fire against — an open call on another.
    open_dirs: dict[tuple[int, int, str], str] = {}
    for s in (
        Signal.objects.filter(outcome=Signal.Outcome.PENDING)
        .order_by("generated_at")
        .values("symbol_id", "service_id", "timeframe", "direction")
    ):
        open_dirs[(s["symbol_id"], s["service_id"], s["timeframe"])] = s["direction"]

    now = timezone.now()
    created = scanned = deduped = invalidated = regime_skipped = 0
    stats = {"gated": 0, "llm_calls": 0, "in_tokens": 0, "out_tokens": 0}
    regime_on = settings.SIGNAL_REGIME_FILTER_ENABLED
    htf_cache: dict[tuple[int, str], str | None] = {}  # (symbol_id, htf) -> trend bias
    forex_is_open = forex_market_open()  # evaluated once per scan
    for sym in symbols:
        # Forex trades ~24/5: skip its symbols on weekends so we don't generate
        # setups off stale closed-market candles (crypto trades 24/7 — unaffected).
        if sym.is_forex and not forex_is_open:
            continue
        for tf in settings.SIGNAL_TIMEFRAMES:
            try:
                candles = get_candles(sym, tf, limit=300)
            except (requests.RequestException, ValueError):
                logger.warning("candle fetch failed: %s %s", sym.ticker, tf)
                continue
            if len(candles) < MIN_CANDLES:
                continue
            indicators = compute_indicators(candles)

            for svc in services:
                pair = (sym.id, svc.id, tf)
                cand = candidate_direction(svc.slug, indicators)
                open_dir = open_dirs.get(pair)
                if open_dir:
                    # Open call exists: only proceed if the trend has flipped.
                    if cand is None or cand == open_dir:
                        deduped += 1
                        continue
                # Regime filter: trending market (ADX) + higher-timeframe
                # agreement, before paying for the LLM call. (cand is None means
                # hybrid mode wouldn't emit anyway; let the engine handle that.)
                if regime_on and cand is not None and not _regime_ok(sym, tf, cand, indicators, htf_cache):
                    regime_skipped += 1
                    continue
                scanned += 1
                try:
                    sig = generate_signal(
                        sym.ticker, tf, svc.slug, svc.name, svc.strategy_focus, indicators,
                        use_pregate=use_pregate, stats=stats,
                    )
                except SignalEngineError as exc:
                    # No API key / misconfig — abort the whole scan, it won't recover.
                    logger.error("Signal engine unavailable: %s", exc)
                    return {"created": created, "scanned": scanned, "error": str(exc)}
                except Exception:
                    logger.exception("signal generation failed: %s %s %s", sym.ticker, tf, svc.slug)
                    continue

                if sig:
                    # If a call is still open and the model agreed with it
                    # (same direction), don't stack a duplicate — only a real
                    # flip warrants a new call.
                    if open_dir and sig["direction"] == open_dir:
                        deduped += 1
                        continue
                    if open_dir:
                        # Trend flipped — close out the now-stale opposite call on
                        # THIS timeframe only (a 4h flip must not touch a 1h call).
                        n = Signal.objects.filter(
                            symbol=sym, service=svc, timeframe=tf,
                            outcome=Signal.Outcome.PENDING,
                        ).update(
                            outcome=Signal.Outcome.INVALIDATED, resolved_at=now
                        )
                        invalidated += n
                    Signal.objects.create(
                        symbol=sym, service=svc, timeframe=tf,
                        generated_at=timezone.now(), **sig,
                    )
                    open_dirs[pair] = sig["direction"]
                    created += 1

    cost = (
        stats["in_tokens"] / 1e6 * settings.OPENAI_PRICE_IN_PER_1M
        + stats["out_tokens"] / 1e6 * settings.OPENAI_PRICE_OUT_PER_1M
    )
    summary = {
        "created": created,
        "scanned": scanned,
        "symbols": len(symbols),
        "deduped": deduped,               # skipped: same-direction call still open (free)
        "invalidated": invalidated,       # stale opposite calls closed on a trend flip
        "regime_skipped": regime_skipped,  # skipped: ranging market / HTF disagreement (free)
        "gated": stats["gated"],          # skipped before any LLM call (free)
        "llm_calls": stats["llm_calls"],  # actual paid OpenAI calls
        "tokens_in": stats["in_tokens"],
        "tokens_out": stats["out_tokens"],
        "est_cost_usd": round(cost, 6),
    }
    logger.info(
        "signal scan: created=%(created)d scanned=%(scanned)d deduped=%(deduped)d "
        "invalidated=%(invalidated)d regime_skipped=%(regime_skipped)d gated=%(gated)d "
        "llm_calls=%(llm_calls)d tokens=%(tokens_in)d/%(tokens_out)d est_cost=$%(est_cost_usd).5f",
        summary,
    )
    return summary


@shared_task(name="apps.signals.tasks.scan_all_signals")
def scan_all_signals():
    # Only the paid LLM scan is gated; the worker/beat still run cheap tasks.
    if not settings.SIGNAL_ENGINE_ENABLED:
        return {"skipped": "SIGNAL_ENGINE_ENABLED is off"}
    return run_scan()


# --- outcome evaluation (Section 13.7, 18) ---

INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800, "12h": 43200, "1d": 86400,
}


def run_evaluation(limit: int | None = None) -> dict:
    """Resolve PENDING signals against the price action that followed.

    A signal stays Active until the trade actually plays out: it resolves only
    when the stop-loss is hit (loss) or a take-profit is hit (win). There is no
    time-based expiry — a call is live until price reaches its stop or a target.
    Cheap — no LLM, just candle fetches.
    """
    pending = (
        Signal.objects.filter(outcome=Signal.Outcome.PENDING)
        .select_related("symbol")
        .order_by("generated_at")
    )
    if limit:
        pending = pending[:limit]

    now = timezone.now()
    resolved = still = 0

    for sig in pending:
        gen_ms = int(sig.generated_at.timestamp() * 1000)
        gen_s = sig.generated_at.timestamp()
        try:
            candles = get_candles_since(sig.symbol, sig.timeframe, gen_ms)
        except (requests.RequestException, ValueError):
            continue
        eval_candles = [c for c in candles if c["time"] > gen_s]
        if not eval_candles:
            still += 1
            continue  # too soon — no candle has closed since generation

        res = walk(
            sig.direction, sig.entry_price, sig.stop_loss,
            [sig.tp1, sig.tp2, sig.tp3, sig.tp4], eval_candles,
        )
        # Resolve only on a hit: a take-profit (win) or the stop-loss (loss).
        # Otherwise the call remains Active, no matter how long it's been open.
        label = outcome_label(res)
        if label:
            # Guard against a race: the candle fetch above is slow, and a
            # concurrent scan may have already closed this call as a breakeven
            # trend-flip invalidation. Only write the SL/TP outcome if the call is
            # STILL open — an invalidated (closed) trade must never be re-recorded
            # as stopped out.
            updated = Signal.objects.filter(
                id=sig.id, outcome=Signal.Outcome.PENDING
            ).update(
                outcome=label, resolved_at=now,
                mfe_pct=res["mfe_pct"], mae_pct=res["mae_pct"],
            )
            resolved += updated  # 0 if it was already closed elsewhere
        else:
            still += 1

    summary = {"resolved": resolved, "still_pending": still}
    logger.info("signal eval: resolved=%(resolved)d still_pending=%(still_pending)d", summary)
    return summary


@shared_task(name="apps.signals.tasks.evaluate_pending_signals")
def evaluate_pending_signals():
    return run_evaluation()


# --- daily housekeeping (keep the database small) ---


def run_purge(days: int | None = None) -> dict:
    """Delete data past the retention window to free database space.

    Removes RESOLVED signals (and, by cascade, their deliveries) older than the
    cutoff, plus already-seen triggered price alerts. Open (PENDING) signals are
    never deleted — an active call must survive until it hits its SL/TP.
    """
    from datetime import timedelta

    from apps.alerts.models import PriceAlert

    days = settings.SIGNAL_RETENTION_DAYS if days is None else days
    cutoff = timezone.now() - timedelta(days=days)

    sig_deleted, _ = (
        Signal.objects.filter(generated_at__lt=cutoff)
        .exclude(outcome=Signal.Outcome.PENDING)
        .delete()
    )
    alerts_deleted, _ = PriceAlert.objects.filter(
        is_active=False, seen=True, triggered_at__lt=cutoff
    ).delete()

    summary = {"days": days, "signals_deleted": sig_deleted, "alerts_deleted": alerts_deleted}
    logger.info(
        "purge: removed signals=%(signals_deleted)d alerts=%(alerts_deleted)d (older than %(days)dd)",
        summary,
    )
    return summary


@shared_task(name="apps.signals.tasks.purge_old_data")
def purge_old_data():
    return run_purge()


# --- Telegram delivery (premium) ------------------------------------------

from datetime import timedelta  # noqa: E402

TELEGRAM_LOOKBACK = timedelta(hours=6)  # don't push signals older than this


def format_signal_for_telegram(s: Signal) -> str:
    """HTML-formatted signal card for a Telegram message."""
    import html

    head = "🟢 BUY" if s.direction == Signal.Direction.BUY else "🔴 SELL"

    def p(x):
        return f"{x:,.2f}" if x is not None else "—"

    lines = [
        f"<b>{head} {html.escape(s.symbol.ticker)}</b> · {html.escape(s.timeframe)} · {s.confidence_pct}% conviction",
        f"<i>{html.escape(s.service.name)}</i>",
        "",
        f"Entry: <b>{p(s.entry_price)}</b>",
        f"Stop:  <b>{p(s.stop_loss)}</b>",
        f"TP1 {p(s.tp1)} · TP2 {p(s.tp2)} · TP3 {p(s.tp3)} · TP4 {p(s.tp4)}",
    ]
    if s.reasoning:
        lines += ["", html.escape(s.reasoning)]
    lines += [
        "",
        "💡 Take partial at TP1, move your stop to entry, let the rest run.",
        "<i>Informational only. Not financial advice.</i>",
    ]
    return "\n".join(lines)


_CLOSURE_STATUS = {
    Signal.Outcome.TP1: "✅ hit TP1",
    Signal.Outcome.TP2: "✅ hit TP2",
    Signal.Outcome.TP3: "✅ hit TP3",
    Signal.Outcome.TP4: "✅ hit TP4",
    Signal.Outcome.SL: "🛑 stopped out",
    Signal.Outcome.INVALIDATED: "⚠️ invalidated — trend flipped",
    Signal.Outcome.EXPIRED: "⌛ expired",
}


def format_closure_for_telegram(s: Signal) -> str:
    """Short 'trade update' message for a signal that has resolved."""
    import html

    side = "BUY" if s.direction == Signal.Direction.BUY else "SELL"
    status = _CLOSURE_STATUS.get(s.outcome, str(s.outcome))
    lines = [
        f"📌 <b>Trade update — {html.escape(s.symbol.ticker)} {side}</b> · {html.escape(s.timeframe)}",
        f"{status}.  <i>{html.escape(s.service.name)}</i>",
    ]
    if s.outcome == Signal.Outcome.INVALIDATED:
        lines.append("Consider closing this trade — a fresh signal follows if a new setup forms.")
    lines.append("<i>Informational only. Not financial advice.</i>")
    return "\n".join(lines)


def run_telegram_close_updates() -> dict:
    """Tell users when a signal they were sent has resolved (TP/SL/invalidated).

    Sends one update per delivered signal (closure_notified guards re-sends). Not
    gated on current premium — if you were sent a call, you're told how it ended.
    Limited to recently-resolved signals so enabling this never floods old ones.
    """
    from apps.accounts import telegram

    if not telegram.is_configured():
        return {"closed": 0}

    cutoff = timezone.now() - timedelta(hours=24)
    pending = (
        TelegramDelivery.objects.filter(closure_notified=False, signal__resolved_at__gte=cutoff)
        .exclude(signal__outcome=Signal.Outcome.PENDING)
        .select_related("signal", "signal__symbol", "signal__service", "user")
    )

    sent = 0
    done_ids = []
    for d in pending:
        chat = d.user.telegram_chat_id
        if not chat:
            done_ids.append(d.id)  # can't deliver (unlinked) — stop tracking
            continue
        if telegram.send_message(chat, format_closure_for_telegram(d.signal)):
            done_ids.append(d.id)
            sent += 1
        # send failure (network): leave unmarked to retry next tick
    if done_ids:
        TelegramDelivery.objects.filter(id__in=done_ids).update(closure_notified=True)

    if sent:
        logger.info("telegram close updates: sent=%d", sent)
    return {"closed": sent}


def run_telegram_push() -> dict:
    """Push new signals to linked premium users' Telegram.

    For each user with a linked chat: pull the new signals from the strategies
    they follow (BUY/SELL, still PENDING, above the confidence threshold, recent)
    that haven't been sent to them yet, capped by their plan's daily quota, and
    send each as a Telegram message. No-op if Telegram isn't configured.
    """
    from django.contrib.auth import get_user_model

    from apps.accounts import telegram

    if not telegram.is_configured():
        return {"skipped": "telegram not configured"}

    # First, tell users about any trades that closed (so "close old" lands before
    # the replacement "new signal" below).
    closed = run_telegram_close_updates().get("closed", 0)

    User = get_user_model()
    now = timezone.now()
    today = now.date()
    min_conf = settings.SIGNAL_MIN_CONFIDENCE

    sent = 0
    for user in User.objects.exclude(telegram_chat_id=""):
        # Telegram delivery is PREMIUM-ONLY. is_premium is expiry-aware, so an
        # expired subscription stops pushes automatically; pushes resume (no
        # re-linking needed) once the user resubscribes. The free tier's small
        # in-app quota does NOT grant Telegram delivery.
        if not user.is_premium:
            continue
        quota = signal_quota_for(user)  # premium: starter 30, pro -1 (unlimited)
        if quota == 0:
            continue
        followed = list(
            UserSignalSubscription.objects.filter(user=user).values_list("service_id", flat=True)
        )
        if not followed:
            continue

        # Only push signals for coins the user watches (same scoping as the feed).
        watched = list(
            WatchlistItem.objects.filter(user=user).values_list("symbol_id", flat=True)
        )
        if not watched:
            continue

        unlimited = quota < 0
        remaining = None
        if not unlimited:
            sent_today = TelegramDelivery.objects.filter(user=user, sent_at__date=today).count()
            remaining = quota - sent_today
            if remaining <= 0:
                continue

        already = TelegramDelivery.objects.filter(user=user).values_list("signal_id", flat=True)
        candidates = (
            Signal.objects.filter(
                service_id__in=followed,
                symbol_id__in=watched,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                outcome=Signal.Outcome.PENDING,
                confidence_pct__gte=min_conf,
                generated_at__gte=now - TELEGRAM_LOOKBACK,
            )
            .exclude(id__in=already)
            .select_related("symbol", "service")
            .order_by("generated_at")
        )
        if not unlimited:
            candidates = candidates[:remaining]

        for sig in candidates:
            if telegram.send_message(user.telegram_chat_id, format_signal_for_telegram(sig)):
                TelegramDelivery.objects.create(user=user, signal=sig)
                sent += 1

    summary = {"sent": sent, "closed": closed}
    logger.info("telegram push: sent=%(sent)d closed=%(closed)d", summary)
    return summary


@shared_task(name="apps.signals.tasks.push_telegram_signals")
def push_telegram_signals():
    return run_telegram_push()
