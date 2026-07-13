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
from django.db.models import F
from django.utils import timezone

from apps.market_data.feeds import get_candles, get_candles_since
from apps.market_data.forex import market_open as forex_market_open
from apps.market_data.models import Symbol
from apps.watchlists.models import WatchlistItem

from . import confluence
from .engine import SignalEngineError, generate_signal
from .evaluate import outcome_label, walk
from .indicators import compute_indicators
from .models import Signal, SignalService, TelegramDelivery, UserSignalSubscription
from .pregate import EMA_STACK_EXEMPT, candidate_direction_for_service
from .quota import SIGNAL_QUOTA_WINDOW, signal_quota_for

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


def _regime_ok(sym, tf: str, direction: str, indicators: dict, htf_cache: dict,
               strategy_slug: str | None = None) -> bool:
    """True if the market is trending (ADX), not chopping (EMA separation), and the
    higher timeframe agrees with `direction`. Fails open on a higher-timeframe fetch
    error so a transient API hiccup doesn't silence the whole feed."""
    adx = indicators.get("adx")
    if adx is None or adx < settings.SIGNAL_ADX_MIN:
        return False
    # Chop filter: in a range the fast EMAs bunch together / flatten. Require EMA9 &
    # EMA21 to be separated by at least SIGNAL_EMA_SEP_MIN_ATR × ATR — a flat/tangled
    # pair means a range, where both tight and wide stops bleed. Set the env to 0 to
    # disable. Breakout strategies are EXEMPT: they legitimately fire from a squeeze,
    # where the EMAs are bunched by design.
    sep_min = settings.SIGNAL_EMA_SEP_MIN_ATR
    if sep_min and strategy_slug not in EMA_STACK_EXEMPT:
        ema9, ema21, atr = indicators.get("ema9"), indicators.get("ema21"), indicators.get("atr")
        if ema9 is not None and ema21 is not None and atr and abs(ema9 - ema21) < sep_min * atr:
            return False  # EMAs bunched → ranging, skip
    # HTF agreement is itself a 200-EMA gate (bias off the 4h/1d 200 EMA). When the
    # 200-EMA trend filter is disabled, drop it too — the ADX + chop filters above
    # still stand, and the Fib zone confirms the entry — so we don't reintroduce the
    # very constraint the filter is meant to remove. It can also be dropped on its
    # own (SIGNAL_HTF_REGIME_ENABLED=False) to keep the 200 EMA deciding trend on the
    # signal's own timeframe without also demanding the higher timeframe agree.
    if not settings.SIGNAL_EMA200_TREND_FILTER or not settings.SIGNAL_HTF_REGIME_ENABLED:
        return True
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
    # Built-in (owner=None) strategies scan every watched symbol. Custom (Pro
    # user-created) strategies scan ONLY their owner's watchlist — see custom_by_symbol.
    system_services = list(SignalService.objects.filter(is_active=True, owner__isnull=True))
    custom_services = list(
        SignalService.objects.filter(is_active=True, owner__isnull=False).select_related("owner")
    )
    if not system_services and not custom_services:
        return {"created": 0, "note": "no active services"}

    # Which symbols each custom strategy runs on: its owner's watchlist.
    from collections import defaultdict
    custom_by_symbol: dict[int, list] = defaultdict(list)
    if custom_services:
        owner_ids = {s.owner_id for s in custom_services}
        owner_symbols: dict[int, set] = defaultdict(set)
        for uid, sid in WatchlistItem.objects.filter(user_id__in=owner_ids).values_list(
            "user_id", "symbol_id"
        ):
            owner_symbols[uid].add(sid)
        for svc in custom_services:
            for sid in owner_symbols.get(svc.owner_id, ()):
                custom_by_symbol[sid].append(svc)

    # Only scan coins someone actually watches (union of all watchlists). If no
    # one has a watchlist, there's nothing to scan — don't generate signals for
    # coins nobody is watching (and don't spend LLM on them).
    watched_ids = list(WatchlistItem.objects.values_list("symbol_id", flat=True).distinct())
    if not watched_ids:
        return {"created": 0, "note": "no watched symbols — nothing to scan"}
    watched = Symbol.objects.filter(is_active=True, id__in=watched_ids)
    limit = settings.SIGNAL_SCAN_SYMBOL_LIMIT if symbol_limit is None else symbol_limit
    if limit:
        # The cap caps the LARGE crypto universe (cost control) but must not starve
        # forex: crypto sorts at sort_order 0..N and forex at 10_000+, so a plain
        # [:limit] on the merged set silently drops every forex pair once there are
        # `limit` watched coins. Cap crypto, then always include the small curated
        # set of watched forex majors (gated by FOREX_ENABLED).
        crypto = list(watched.filter(asset_class=Symbol.AssetClass.CRYPTO)[:limit])
        forex = (
            list(watched.filter(asset_class=Symbol.AssetClass.FOREX))
            if settings.FOREX_ENABLED else []
        )
        symbols = crypto + forex
        # A custom strategy's owner may watch a crypto symbol beyond the cost cap —
        # include those so the owner's own strategy isn't silently skipped.
        if custom_by_symbol:
            have = {s.id for s in symbols}
            extra = set(custom_by_symbol) - have
            if extra:
                symbols += list(watched.filter(id__in=extra))
    else:
        symbols = list(watched)

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

    # Re-entry cooldown (C): most recent signal time per (symbol, service, timeframe,
    # direction). Once a call closes the open_dirs dedup no longer applies, so in a
    # runaway trend the next scan would re-issue the same BUY immediately; this
    # spaces same-direction re-entries by SIGNAL_REENTRY_COOLDOWN_BARS bars. Keyed
    # WITH direction so a real flip is never delayed. Only recent rows matter (the
    # cooldown is a few bars), so the lookup is bounded to the longest possible window.
    from datetime import timedelta

    from django.db.models import Max

    cooldown_bars = settings.SIGNAL_REENTRY_COOLDOWN_BARS
    last_sig_at: dict[tuple[int, int, str, str], object] = {}
    if cooldown_bars > 0:
        longest_bar = max(
            (INTERVAL_SECONDS.get(tf, 3600) for tf in settings.SIGNAL_TIMEFRAMES),
            default=3600,
        )
        cutoff = now - timedelta(seconds=longest_bar * cooldown_bars)
        for s in (
            Signal.objects.filter(generated_at__gte=cutoff)
            .values("symbol_id", "service_id", "timeframe", "direction")
            .annotate(last=Max("generated_at"))
        ):
            last_sig_at[
                (s["symbol_id"], s["service_id"], s["timeframe"], s["direction"])
            ] = s["last"]

    created = scanned = deduped = cooled = invalidated = regime_skipped = 0
    stats = {"gated": 0, "llm_calls": 0, "in_tokens": 0, "out_tokens": 0}
    regime_on = settings.SIGNAL_REGIME_FILTER_ENABLED
    htf_cache: dict[tuple[int, str], str | None] = {}  # (symbol_id, htf) -> trend bias
    forex_is_open = forex_market_open()  # evaluated once per scan; also our
    # "is it the weekend window" signal (Fri 21:00 → Sun 21:00 UTC).
    for sym in symbols:
        # Weekend window: forex is always skipped (market closed). Crypto trades
        # 24/7, but weekend crypto setups backtest far worse — thin liquidity and
        # chop produce fakeouts that trip stops (live: ~35% weekend win-rate vs
        # ~76% weekday). So optionally skip crypto over the weekend too. Using the
        # forex window (not a calendar weekend) also trims the thin Friday-night
        # session, which underperforms as well.
        if not forex_is_open and (sym.is_forex or settings.SIGNAL_SKIP_CRYPTO_WEEKEND):
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

            for svc in system_services + custom_by_symbol.get(sym.id, []):
                pair = (sym.id, svc.id, tf)
                cand = candidate_direction_for_service(svc, indicators)
                open_dir = open_dirs.get(pair)
                if open_dir:
                    # Open call exists: only proceed if the trend has flipped.
                    if cand is None or cand == open_dir:
                        deduped += 1
                        continue
                elif cooldown_bars and cand is not None:
                    # No open call, but a same-direction one may have just closed.
                    # Hold off re-issuing for the cooldown window (anti-chase, C).
                    last = last_sig_at.get((sym.id, svc.id, tf, cand))
                    if last is not None:
                        bar_s = INTERVAL_SECONDS.get(tf, 3600)
                        if (now - last).total_seconds() < cooldown_bars * bar_s:
                            cooled += 1
                            continue
                # Regime filter: trending market (ADX) + higher-timeframe
                # agreement, before paying for the LLM call. (cand is None means
                # hybrid mode wouldn't emit anyway; let the engine handle that.)
                # Custom strategies bypass the regime filter (they fire purely on the
                # user's own conditions — see candidate_direction_for_service).
                if (
                    regime_on and cand is not None and not svc.is_custom
                    and not _regime_ok(sym, tf, cand, indicators, htf_cache, svc.slug)
                ):
                    regime_skipped += 1
                    continue
                scanned += 1
                try:
                    sig = generate_signal(
                        sym.ticker, tf, svc.slug, svc.name, svc.strategy_focus, indicators,
                        use_pregate=use_pregate, stats=stats, asset_class=sym.asset_class,
                        rule_config=svc.rule_config,
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
        "cooled": cooled,                 # skipped: same-direction re-entry within cooldown (free)
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
        "cooled=%(cooled)d invalidated=%(invalidated)d regime_skipped=%(regime_skipped)d gated=%(gated)d "
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

    A signal stays Active while the trade is genuinely live: it resolves when the stop
    is hit (loss), the final target is reached (win), or — after
    ``settings.SIGNAL_EVAL_BARS`` candles — it simply ran out of time.

    That last clause is load-bearing, not housekeeping. The scan won't issue a new
    signal while a strategy has an open call on that symbol+timeframe, so every open
    call holds a slot; without expiry they accumulated indefinitely (712 at the point
    this was found) until the scanned coins had almost no free slots left, new signals
    stopped being generated, and delivery — which needs SIGNAL_CONFLUENCE_MIN *fresh*
    strategies to agree — starved. It also bounded the evaluator, which fetches candles
    once per open call on every pass.

    A timed-out call that already banked TP1/TP2 resolves at that banked level (the
    scale-out P&L is locked in — see §19.2), NOT as EXPIRED. Only a call that never
    reached a target expires flat. Cheap — no LLM, just candle fetches.
    """
    pending = (
        Signal.objects.filter(outcome=Signal.Outcome.PENDING)
        .select_related("symbol")
        .order_by("generated_at")
    )
    if limit:
        pending = pending[:limit]

    now = timezone.now()
    resolved = still = expired = 0

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
            [t for t in (sig.tp1, sig.tp2, sig.tp3, sig.tp4) if t is not None], eval_candles,
            breakeven_after_tp1=True,
        )
        # "Let winners run" (§19.2): don't lock a winner in the moment it tags TP1 —
        # keep it Active so it can reach TP2/TP3, and resolve only when the trade is
        # actually terminal (the stop or breakeven-stop is hit, or the last TP is
        # reached). A trade that has merely tagged TP1 and is still running is NOT
        # terminal, so it stays pending. `outcome_label` records the furthest target
        # reached, so a runner closed at breakeven after TP1 still books a TP1 win.
        label = outcome_label(res)
        # Stamp the moment a NEW target was tagged, so the dashboard can timestamp the
        # event instead of guessing. Only when it actually advanced — a re-evaluation
        # that finds the same best_tp must not keep bumping the clock.
        tagged = {"best_tp_at": now} if res["best_tp"] > sig.best_tp else {}
        if label and res["terminal"]:
            # Guard against a race: the candle fetch above is slow, and a
            # concurrent scan may have already closed this call as a breakeven
            # trend-flip invalidation. Only write the SL/TP outcome if the call is
            # STILL open — an invalidated (closed) trade must never be re-recorded
            # as stopped out.
            updated = Signal.objects.filter(
                id=sig.id, outcome=Signal.Outcome.PENDING
            ).update(
                outcome=label, resolved_at=now, best_tp=res["best_tp"],
                mfe_pct=res["mfe_pct"], mae_pct=res["mae_pct"], **tagged,
            )
            resolved += updated  # 0 if it was already closed elsewhere
        elif len(eval_candles) >= settings.SIGNAL_EVAL_BARS:
            # Out of time. Close it at whatever it actually banked: a call that tagged
            # TP1/TP2 books that (the partial is real money), one that never reached a
            # target expires flat. Either way the slot is freed so the strategy can
            # signal on this symbol again.
            Signal.objects.filter(id=sig.id, outcome=Signal.Outcome.PENDING).update(
                outcome=label or Signal.Outcome.EXPIRED, resolved_at=now,
                best_tp=res["best_tp"],
                mfe_pct=res["mfe_pct"], mae_pct=res["mae_pct"], **tagged,
            )
            expired += 1
        else:
            # Still running. Record how far it has got anyway: a trade that has
            # banked TP1/TP2 and is still chasing TP3 is NOT terminal, but the user
            # was told to take a partial and move their stop to entry the moment TP1
            # tagged — so that has to be visible (and pushable) now, not at closure.
            Signal.objects.filter(id=sig.id, outcome=Signal.Outcome.PENDING).update(
                best_tp=res["best_tp"],
                mfe_pct=res["mfe_pct"], mae_pct=res["mae_pct"], **tagged,
            )
            still += 1

    summary = {"resolved": resolved, "timed_out": expired, "still_pending": still}
    logger.info(
        "signal eval: resolved=%(resolved)d timed_out=%(timed_out)d "
        "still_pending=%(still_pending)d", summary,
    )
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

# How long after a signal resolves we'll still send its "trade update". Sized to
# outlast any plausible worker/beat outage — the window is anchored to the signal's
# (immutable) resolved_at, so a shorter one silently lost closures whenever the
# task didn't run for a day. Anything that ages past this is retired unnotified
# rather than left pending forever: a stop-out alert arriving days late is worse
# than none, and retiring it stops a widened window from ever resurrecting a flood
# of ancient closures.
CLOSURE_SEND_WINDOW = timedelta(hours=72)


def _fmt_price(x) -> str:
    """Price with precision suited to magnitude, so sub-dollar coins (DOGE, kPEPE)
    and FX pairs don't collapse to '0.00' under a fixed 2-dp format."""
    if x is None:
        return "—"
    a = abs(x)
    d = 2 if a >= 100 else 4 if a >= 1 else 5 if a >= 0.01 else 8
    return f"{x:,.{d}f}"


def format_signal_for_telegram(s: Signal) -> str:
    """HTML-formatted signal card for a Telegram message."""
    import html

    head = "🟢 BUY" if s.direction == Signal.Direction.BUY else "🔴 SELL"
    p = _fmt_price

    lines = [
        f"<b>{head} {html.escape(s.symbol.ticker)}</b> · {html.escape(s.timeframe)} · {s.confidence_pct}% conviction",
        f"<i>{html.escape(s.service.name)}</i>",
    ]
    # Confluence badge: when several strategies agree, that's the headline value.
    n_agree = getattr(s, "confluence_count", 1)
    if n_agree >= 2:
        svcs = ", ".join(html.escape(name) for name in getattr(s, "confluence_services", []))
        lines.append(f"📊 <b>{n_agree} strategies agree</b>: {svcs}")
    tp_line = f"TP1 {p(s.tp1)} · TP2 {p(s.tp2)} · TP3 {p(s.tp3)}"
    if s.tp4 is not None:  # legacy signals may still carry a TP4
        tp_line += f" · TP4 {p(s.tp4)}"
    lines += [
        "",
        f"Entry: <b>{p(s.entry_price)}</b>",
        f"Stop:  <b>{p(s.stop_loss)}</b>",
        tp_line,
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
    p = _fmt_price

    lines = [
        f"📌 <b>Trade update — {html.escape(s.symbol.ticker)} {side}</b> · {html.escape(s.timeframe)}",
        f"{status}.  <i>{html.escape(s.service.name)}</i>",
        "",
        f"Entry: <b>{p(s.entry_price)}</b>",
    ]

    # Show only the level relevant to how the trade closed, alongside entry.
    tp_hit = {
        Signal.Outcome.TP1: ("TP1", s.tp1),
        Signal.Outcome.TP2: ("TP2", s.tp2),
        Signal.Outcome.TP3: ("TP3", s.tp3),
        Signal.Outcome.TP4: ("TP4", s.tp4),
    }
    if s.outcome in tp_hit:
        label, price = tp_hit[s.outcome]
        lines.append(f"{label} hit: <b>{p(price)}</b>")
        # Scale-out model (§19.2): a partial is banked at each target and the stop
        # trails to breakeven after TP1, so a TP1/TP2 close means the runner came
        # back to breakeven with the earlier third(s) already secured.
        if s.outcome == Signal.Outcome.TP1:
            lines.append("<i>First target banked; runner closed at breakeven.</i>")
        elif s.outcome == Signal.Outcome.TP2:
            lines.append("<i>TP1 & TP2 banked; runner closed at breakeven.</i>")
        elif s.outcome == Signal.Outcome.TP3:
            lines.append("<i>Full run — all three targets hit.</i>")
    else:
        # SL / INVALIDATED / EXPIRED — the stop is the relevant risk level.
        lines.append(f"Stop loss: <b>{p(s.stop_loss)}</b>")

    if s.outcome == Signal.Outcome.INVALIDATED:
        lines.append("")
        lines.append("Consider closing this trade — a fresh signal follows if a new setup forms.")
    lines.append("<i>Informational only. Not financial advice.</i>")
    return "\n".join(lines)


def format_progress_for_telegram(s: Signal, tp: int) -> str:
    """'Target tagged' message for a trade that is still OPEN.

    The entry card tells the user to take a partial at TP1 and move their stop to
    entry — advice that is only actionable at the moment the target is tagged. Under
    "let winners run" (§19.2) the trade stays open until TP3 or the breakeven stop,
    so without this the user would first hear about TP1 at closure, hours late.
    """
    import html

    side = "BUY" if s.direction == Signal.Direction.BUY else "SELL"
    p = _fmt_price
    level = {1: s.tp1, 2: s.tp2, 3: s.tp3, 4: s.tp4}.get(tp)

    lines = [
        f"🎯 <b>Target hit — {html.escape(s.symbol.ticker)} {side}</b> · {html.escape(s.timeframe)}",
        f"✅ <b>TP{tp}</b> tagged at <b>{p(level)}</b>.  <i>{html.escape(s.service.name)}</i>",
        "",
        f"Entry: <b>{p(s.entry_price)}</b>",
    ]
    if tp == 1:
        lines += [
            "",
            "💡 Take your partial at TP1 and move your stop to entry (breakeven).",
            f"Trade stays open — runner targets TP2 {p(s.tp2)} · TP3 {p(s.tp3)}.",
        ]
    elif tp == 2:
        lines += [
            "",
            "💡 Bank the second partial. Stop stays at entry.",
            f"Trade stays open — runner targets TP3 {p(s.tp3)}.",
        ]
    else:
        # The final target IS terminal, so the closure message covers it. Kept for
        # completeness in case the ladder ever grows past TP3.
        lines += ["", "💡 Final target reached."]
    lines.append("<i>Informational only. Not financial advice.</i>")
    return "\n".join(lines)


def run_telegram_progress_updates() -> dict:
    """Push 'TP tagged' notices for trades that are still OPEN.

    Fires whenever the evaluator has recorded a higher ``Signal.best_tp`` than we've
    already told this user about (``TelegramDelivery.tp_notified``). Only for still-
    PENDING signals: once a trade resolves, ``run_telegram_close_updates`` is the one
    that speaks, so a trade never gets both a "TP2 tagged" and a "hit TP2" message
    for the same event.

    Sends one message per newly reached target (TP1 then TP2), not just the highest,
    so a trade that races through both in one evaluator pass still explains itself.
    """
    from apps.accounts import telegram

    if not telegram.is_configured():
        return {"progressed": 0}

    pending = (
        TelegramDelivery.objects.filter(
            closure_notified=False,
            signal__outcome=Signal.Outcome.PENDING,
            signal__best_tp__gt=F("tp_notified"),
        )
        .select_related("signal", "signal__symbol", "signal__service", "user")
    )

    sent = 0
    for d in pending:
        chat = d.user.telegram_chat_id
        if not chat or not d.user.telegram_active:
            continue  # unlinked or delivery off — closure path will retire it
        best = d.signal.best_tp
        reached = d.tp_notified
        for tp in range(d.tp_notified + 1, best + 1):
            if not telegram.send_message(chat, format_progress_for_telegram(d.signal, tp)):
                break  # network failure — retry the rest next tick
            reached = tp
            sent += 1
        if reached > d.tp_notified:
            TelegramDelivery.objects.filter(id=d.id).update(tp_notified=reached)

    if sent:
        logger.info("telegram progress updates: sent=%d", sent)
    return {"progressed": sent}


def run_telegram_close_updates() -> dict:
    """Tell users when a signal they were sent has resolved (TP/SL/invalidated).

    Sends one update per delivered signal (closure_notified guards re-sends). Not
    gated on current premium — if you were sent a call, you're told how it ended.

    Closures resolved longer ago than CLOSURE_SEND_WINDOW are retired unnotified
    (see below) rather than silently skipped, so an outage delays updates instead
    of losing them.
    """
    from apps.accounts import telegram

    if not telegram.is_configured():
        return {"closed": 0, "retired": 0}

    cutoff = timezone.now() - CLOSURE_SEND_WINDOW

    # Too stale to be worth sending. Mark them done so they stop counting as
    # pending work — and so widening the window later can't resurrect them.
    # PENDING signals have a null resolved_at and never match `__lt`.
    retired = (
        TelegramDelivery.objects.filter(closure_notified=False, signal__resolved_at__lt=cutoff)
        .exclude(signal__outcome=Signal.Outcome.PENDING)
        .update(closure_notified=True)
    )
    if retired:
        logger.warning(
            "telegram close updates: retired %d stale closure(s) older than %s "
            "without sending (worker likely lagged)",
            retired, CLOSURE_SEND_WINDOW,
        )

    # EXPIRED means the call simply ran out of time without touching its stop or a
    # target — nothing happened, and there is nothing for the user to do. Retire those
    # silently instead of pushing "⌛ expired" for each. Without this, re-enabling
    # expiry would drain the accumulated backlog of stale open calls straight into
    # users' Telegram as a burst of hundreds of non-events. A timed-out call that DID
    # bank TP1/TP2 resolves as TP1/TP2, not EXPIRED, so real outcomes still send.
    retired += (
        TelegramDelivery.objects.filter(
            closure_notified=False, signal__outcome=Signal.Outcome.EXPIRED
        ).update(closure_notified=True)
    )

    pending = (
        TelegramDelivery.objects.filter(closure_notified=False, signal__resolved_at__gte=cutoff)
        .exclude(signal__outcome=Signal.Outcome.PENDING)
        .exclude(signal__outcome=Signal.Outcome.EXPIRED)
        .select_related("signal", "signal__symbol", "signal__service", "user")
    )

    sent = 0
    done_ids = []
    for d in pending:
        chat = d.user.telegram_chat_id
        if not chat or not d.user.telegram_active:
            done_ids.append(d.id)  # unlinked or delivery off — stop tracking
            continue
        if telegram.send_message(chat, format_closure_for_telegram(d.signal)):
            done_ids.append(d.id)
            sent += 1
        # send failure (network): leave unmarked to retry next tick
    if done_ids:
        TelegramDelivery.objects.filter(id__in=done_ids).update(closure_notified=True)

    if sent:
        logger.info("telegram close updates: sent=%d", sent)
    return {"closed": sent, "retired": retired}


def run_telegram_push() -> dict:
    """Push new signals to linked premium users' Telegram.

    For each user with a linked chat: pull the new signals from the strategies
    they follow (BUY/SELL, still PENDING, above the confidence threshold, recent)
    that haven't been sent to them yet, capped by their plan's weekly quota
    (rolling 7-day window), and send each as a Telegram message. No-op if
    Telegram isn't configured.
    """
    from django.contrib.auth import get_user_model

    from apps.accounts import telegram

    if not telegram.is_configured():
        return {"skipped": "telegram not configured"}

    # First, tell users about any trades that closed (so "close old" lands before
    # the replacement "new signal" below), then about open trades that tagged a
    # target — that's the "take your partial now" notice, and it's time-sensitive.
    closed = run_telegram_close_updates().get("closed", 0)
    progressed = run_telegram_progress_updates().get("progressed", 0)

    # Weekend window (Fri 21:00 → Sun 21:00 UTC, i.e. forex closed): suppress ALL
    # NEW signal pushes. Users asked for this — thin weekend liquidity produces poor
    # setups, and the scan already stops generating over the weekend, but a signal
    # generated late Friday can still fall inside the delivery lookback. Only the
    # "Trade update" closures above go out on weekends. Mirrors run_scan's skip.
    if not forex_market_open():
        logger.info("telegram push: weekend window — new signals suppressed, closures only")
        return {"sent": 0, "closed": closed, "weekend": True}

    User = get_user_model()
    now = timezone.now()
    week_cutoff = now - SIGNAL_QUOTA_WINDOW  # rolling 7-day quota window

    sent = 0
    for user in User.objects.filter(telegram_active=True).exclude(telegram_chat_id=""):
        # Telegram delivery is PREMIUM-ONLY. is_premium is expiry-aware, so an
        # expired subscription stops pushes automatically; pushes resume (no
        # re-linking needed) once the user resubscribes. The free tier's small
        # in-app quota does NOT grant Telegram delivery.
        if not user.is_premium:
            continue
        quota = signal_quota_for(user)  # premium: starter 400/wk, pro -1 (unlimited)
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
            sent_this_week = TelegramDelivery.objects.filter(
                user=user, sent_at__gte=week_cutoff
            ).count()
            remaining = quota - sent_this_week
            if remaining <= 0:
                continue

        # Dedup at the TRADE grain — (symbol, timeframe, direction, entry_price), so
        # every strategy firing the SAME setup (they share the entry) counts as one
        # trade. Keyed on what was SENT within the lookback window, NOT on the rep
        # still being PENDING: a fast first rep that hit TP/SL (or got invalidated)
        # was dropping its group from a PENDING-only guard, letting a sibling rep
        # re-send the same trade (the "two signals per symbol" bug). One send per
        # trade per lookback; a genuinely new trade has a different entry, so it
        # still gets through.
        delivered_trades = set(
            TelegramDelivery.objects.filter(
                user=user, sent_at__gte=now - TELEGRAM_LOOKBACK,
            ).values_list(
                "signal__symbol_id", "signal__timeframe", "signal__direction", "signal__entry_price",
            )
        )
        candidates = list(
            Signal.objects.filter(
                confluence.deliverable_q(),  # custom strategies bypass the conf floor
                service_id__in=followed,
                symbol_id__in=watched,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                outcome=Signal.Outcome.PENDING,
                generated_at__gte=now - TELEGRAM_LOOKBACK,
            )
            .select_related("symbol", "service")
        )
        # Collapse by confluence: one card per (symbol, timeframe), surfaced only
        # when enough strategies agree. Drop trades already sent in the window, then
        # send oldest-first so the quota fills chronologically.
        reps = [
            r for r in confluence.collapse(candidates)
            if (r.symbol_id, r.timeframe, r.direction, r.entry_price) not in delivered_trades
        ]
        reps.sort(key=lambda s: s.generated_at)
        if not unlimited:
            reps = reps[:remaining]

        for sig in reps:
            key = (sig.symbol_id, sig.timeframe, sig.direction, sig.entry_price)
            if key in delivered_trades:  # guard against a duplicate within this run
                continue
            if telegram.send_message(user.telegram_chat_id, format_signal_for_telegram(sig)):
                TelegramDelivery.objects.create(user=user, signal=sig)
                delivered_trades.add(key)
                sent += 1

    summary = {"sent": sent, "closed": closed, "progressed": progressed}
    logger.info(
        "telegram push: sent=%(sent)d closed=%(closed)d progressed=%(progressed)d", summary
    )
    return summary


@shared_task(name="apps.signals.tasks.push_telegram_signals")
def push_telegram_signals():
    return run_telegram_push()
