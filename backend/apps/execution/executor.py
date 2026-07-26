"""Auto-trade executor — turn delivered signals into real Bybit orders.

Mirrors ``apps.signals.tasks.run_telegram_push``: for each user who has switched on
auto-trade (and is premium), find the same confluence-collapsed, quota-worthy signals
that would be pushed to them, and place a bracket order per fresh trade — sized by
``apps.execution.sizing.plan_order`` and fenced by a per-user risk envelope.

Design stance (deliberate, because this moves real money):
- OFF unless ``settings.AUTO_TRADE_ENABLED`` AND the user's credential is enabled.
- Crypto only. Forex signals (Yahoo feed) aren't tradeable here and are skipped.
- Fail-safe & per-user isolated: any error for one user records a REJECTED row and
  moves on — one bad key never stops the batch.
- Idempotent: dedup on (user, signal) in the DB and on a deterministic orderLinkId
  at Bybit, so a retried scan never double-fills.
- Place-only: one entry + full-position stop-loss + a single take-profit bracket
  (which target is set by ``AUTO_TRADE_TP_LEVEL``). The 50/25/25 partial scale-out
  the signal card describes (§19.2) is NOT replicated as multiple conditional orders
  — that's a documented follow-up, not this pass.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.market_data.models import Symbol
from apps.signals import confluence
from apps.signals.models import Signal, UserSignalSubscription
from apps.watchlists.models import WatchlistItem

from . import crypto
from .bybit import BybitClient, BybitError
from .models import BrokerCredential, TradeExecution
from .sizing import plan_order, split_scaleout

logger = logging.getLogger("execution")

# Don't act on a signal older than this — a stale setup's entry no longer reflects
# the market. Belt-and-braces alongside the per-signal age gate below.
TRADE_LOOKBACK = timedelta(hours=6)

# The 50/25/25 scale-out of §19.2: bank half at TP1, a quarter at TP2, a quarter at
# TP3, mapped to (sig.tp1, sig.tp2, sig.tp3). Placed as reduce-only limit rungs; the
# stop trails to breakeven once the first rung fills (handled in run_reconcile).
SCALEOUT_FRACTIONS = (0.5, 0.25, 0.25)


def _bybit_symbol(sym: Symbol) -> str | None:
    """Map an internal crypto Symbol to a Bybit USDT-perp symbol (BTC → BTCUSDT).
    Returns None for forex / anything not tradeable on Bybit linear perps."""
    if sym.is_forex or not sym.hl_coin:
        return None
    return f"{sym.hl_coin.upper()}USDT"


def _bybit_side(direction: str) -> str:
    return "Buy" if direction == Signal.Direction.BUY else "Sell"


def _tp_for(sig: Signal) -> float | None:
    """The single take-profit level to bracket to, per AUTO_TRADE_TP_LEVEL."""
    level = getattr(settings, "AUTO_TRADE_TP_LEVEL", "tp2")
    return {"tp1": sig.tp1, "tp2": sig.tp2, "tp3": sig.tp3}.get(level, sig.tp2)


def _deliverable_reps_for(user, now) -> list[Signal]:
    """The confluence-collapsed signals this user would be delivered right now —
    same scoping as the Telegram push: followed strategies, watched symbols, above
    the confidence floor, PENDING and recent. Crypto only (forex isn't tradeable)."""
    followed = list(
        UserSignalSubscription.objects.filter(user=user)
        .filter(Q(service__owner__isnull=True) | Q(service__owner=user))
        .values_list("service_id", flat=True)
    )
    if not followed:
        return []
    watched = list(
        WatchlistItem.objects.filter(user=user).values_list("symbol_id", flat=True)
    )
    if not watched:
        return []
    candidates = list(
        Signal.objects.filter(
            confluence.deliverable_q(),
            service_id__in=followed,
            symbol_id__in=watched,
            symbol__asset_class=Symbol.AssetClass.CRYPTO,
            direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
            outcome=Signal.Outcome.PENDING,
            generated_at__gte=now - TRADE_LOOKBACK,
        ).select_related("symbol", "service")
    )
    reps = confluence.collapse(candidates)
    reps.sort(key=lambda s: s.generated_at)  # oldest first — fill slots chronologically
    return reps


def _open_position_count(user) -> int:
    return TradeExecution.objects.filter(
        user=user, status=TradeExecution.Status.OPEN
    ).count()


def _trades_today(user, now) -> int:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return TradeExecution.objects.filter(user=user, created_at__gte=day_start).count()


def _record(user, sig, bybit_symbol, status, *, reason="", plan=None,
            take_profit=None, order_link_id="", bybit_order_id="",
            entry_price=None, scaleout=False) -> TradeExecution | None:
    """Create a TradeExecution, tolerating the (user, signal) dedup race — if a
    concurrent run already recorded this trade, return None.

    ``entry_price`` defaults to the signal's entry but is overridden with the live
    price we actually sized/placed at, so the breakeven-stop move keys off the real
    entry rather than the (possibly drifted) signal price."""
    fields = dict(
        user=user, signal=sig, bybit_symbol=bybit_symbol,
        direction=sig.direction, timeframe=sig.timeframe, status=status,
        reason=reason[:200],
        entry_price=sig.entry_price if entry_price is None else entry_price,
        stop_loss=sig.stop_loss, take_profit=take_profit, scaleout=scaleout,
        order_link_id=order_link_id, bybit_order_id=bybit_order_id,
    )
    if plan is not None:
        fields.update(
            leverage=plan.leverage, qty=plan.qty, notional=plan.notional,
            margin=plan.margin, liq_price=plan.liq_price,
        )
    try:
        return TradeExecution.objects.create(**fields)
    except IntegrityError:
        return None  # already acted on this (user, signal) — dedup won the race


def _ensure_isolated(client: BybitClient) -> None:
    """Put the account in isolated margin, reading first so the write is skipped when
    it's already right.

    The read matters for more than politeness: Bybit refuses a mode switch while any
    position or order is open, so once the first trade is live an unconditional write
    would fail every subsequent placement. Reading first means the switch happens once
    (flat account) and later trades short-circuit on 'already isolated'. Memoised per
    client so a batch of signals for one user costs a single extra request.
    """
    if getattr(client, "_isolated_ok", False):
        return
    if client.get_margin_mode() != BybitClient.ISOLATED:
        client.set_margin_mode(BybitClient.ISOLATED)
    client._isolated_ok = True


def _place_one(client: BybitClient, cred: BrokerCredential, sig: Signal,
               bybit_symbol: str, now) -> str:
    """Size, place, and record a single trade. Returns a short status string used
    only for batch counting ('placed' | 'skipped' | 'rejected' | 'deduped')."""
    user = cred.user
    base_link = f"pc-{sig.id}-{user.id}"

    # Live price + instrument filters.
    try:
        instrument = client.get_instrument(bybit_symbol)
        last_price = client.get_last_price(bybit_symbol)
    except BybitError as exc:
        _record(user, sig, bybit_symbol, TradeExecution.Status.REJECTED, reason=str(exc))
        return "rejected"

    # Slippage guard: if price has already run past the signal's entry by more than
    # the allowed %, the setup is no longer the one we'd size — skip rather than
    # chase. Uses the live price as the true entry reference.
    drift = abs(last_price - sig.entry_price) / sig.entry_price if sig.entry_price else 1.0
    if drift > settings.AUTO_TRADE_MAX_SLIPPAGE_PCT / 100.0:
        _record(user, sig, bybit_symbol, TradeExecution.Status.SKIPPED,
                reason=f"slippage {drift*100:.2f}% > {settings.AUTO_TRADE_MAX_SLIPPAGE_PCT}%")
        return "skipped"

    # Size the position: lowest leverage that clears exchange minimums while keeping
    # liquidation beyond the stop. Sized against the live price (the real fill).
    plan = plan_order(
        allocation_usd=cred.margin_per_trade_usd,
        entry_price=last_price,
        stop_loss=sig.stop_loss,
        min_order_qty=instrument.min_order_qty,
        qty_step=instrument.qty_step,
        min_notional=instrument.min_notional,
        max_leverage=min(cred.max_leverage, instrument.max_leverage),
    )
    if not plan.ok:
        _record(user, sig, bybit_symbol, TradeExecution.Status.SKIPPED, reason=plan.reason)
        return "skipped"

    # Scale-out ladder vs single-TP bracket. The ladder needs each 50/25/25 tranche to
    # clear the exchange minimum; on the tiny fixed-margin sizes this app places that
    # often isn't possible, so split_scaleout returns all-zero and we fall back to a
    # single full-position TP (AUTO_TRADE_TP_LEVEL) set right on the entry order.
    tranches = (
        split_scaleout(plan.qty, SCALEOUT_FRACTIONS,
                       qty_step=instrument.qty_step, min_order_qty=instrument.min_order_qty)
        if settings.AUTO_TRADE_SCALEOUT else []
    )
    ladder = any(t > 0 for t in tranches)
    entry_tp = None if ladder else _tp_for(sig)  # single-TP path brackets on the entry
    display_tp = sig.tp1 if ladder else entry_tp  # what we record for the card/history

    # Isolated margin is what makes the sizing safe: plan_order's liquidation estimate
    # assumes isolated ((1/L) - mmr), and only under isolated is the loss actually
    # capped at this trade's margin. Under cross, one bad fill can draw on the whole
    # balance. So enforce it BEFORE placing, and refuse the trade if we can't — placing
    # anyway would mean trading a risk model the account doesn't implement.
    if settings.AUTO_TRADE_ISOLATED_MARGIN:
        try:
            _ensure_isolated(client)
        except BybitError as exc:
            _record(user, sig, bybit_symbol, TradeExecution.Status.REJECTED,
                    reason=f"margin mode: {exc}"[:200])
            return "rejected"

    try:
        client.set_leverage(bybit_symbol, plan.leverage)
        order_id = client.place_market_bracket(
            bybit_symbol, _bybit_side(sig.direction), plan.qty,
            stop_loss=sig.stop_loss, take_profit=entry_tp,
            order_link_id=base_link,
        )
    except BybitError as exc:
        # Duplicate orderLinkId (110072) means a prior run already placed this exact
        # trade — record it OPEN, not rejected, so dedup holds across retries.
        if exc.code == 110072:
            _record(user, sig, bybit_symbol, TradeExecution.Status.OPEN, plan=plan,
                    take_profit=display_tp, order_link_id=base_link,
                    entry_price=last_price, scaleout=ladder)
            return "deduped"
        _record(user, sig, bybit_symbol, TradeExecution.Status.REJECTED, reason=str(exc),
                plan=plan, take_profit=display_tp, order_link_id=base_link,
                entry_price=last_price)
        return "rejected"

    # Entry is open — lay the take-profit rungs (only in the ladder path).
    if ladder:
        close_side = "Sell" if sig.direction == Signal.Direction.BUY else "Buy"
        tp_prices = [sig.tp1, sig.tp2, sig.tp3]
        for idx, (price, tqty) in enumerate(zip(tp_prices, tranches), start=1):
            if tqty <= 0 or price is None:
                continue
            try:
                client.place_reduce_limit(
                    bybit_symbol, close_side, tqty, price,
                    order_link_id=f"{base_link}-tp{idx}",
                )
            except BybitError as exc:
                logger.warning("auto-trade: TP%d rung failed for user %s %s: %s",
                               idx, user.id, bybit_symbol, exc)

    row = _record(user, sig, bybit_symbol, TradeExecution.Status.OPEN, plan=plan,
                  take_profit=display_tp, order_link_id=base_link, bybit_order_id=order_id,
                  entry_price=last_price, scaleout=ladder)
    if row is None:
        return "deduped"
    logger.info(
        "auto-trade placed: user=%s %s %s qty=%s lev=%sx ladder=%s order=%s",
        user.id, sig.direction, bybit_symbol, plan.qty, plan.leverage, ladder, order_id,
    )
    return "placed"


def _run_for_user(cred: BrokerCredential, now) -> dict:
    user = cred.user
    counts = {"placed": 0, "skipped": 0, "rejected": 0, "deduped": 0}

    # Build the client once (validates the encryption key is usable).
    try:
        client = BybitClient(cred.api_key, cred.api_secret, testnet=cred.testnet)
    except crypto.BrokerCryptoError as exc:
        logger.warning("auto-trade: bad credentials for user %s: %s", user.id, exc)
        return counts

    reps = _deliverable_reps_for(user, now)
    if not reps:
        return counts

    # Drop signals we've already acted on (dedup) and non-tradeable ones up front.
    acted_signal_ids = set(
        TradeExecution.objects.filter(
            user=user, signal_id__in=[s.id for s in reps]
        ).values_list("signal_id", flat=True)
    )

    open_positions = _open_position_count(user)
    trades_today = _trades_today(user, now)
    max_open = cred.effective_max_open_positions
    max_daily = cred.effective_max_daily_trades
    max_age = settings.AUTO_TRADE_MAX_SIGNAL_AGE_SEC
    # Symbols this user already has a live position on. Bybit one-way mode NETS two
    # trades on the same symbol into a single position, which would break the
    # per-trade scale-out and breakeven tracking — so at most one open auto-trade per
    # symbol per user.
    open_symbols = set(
        TradeExecution.objects.filter(user=user, status=TradeExecution.Status.OPEN)
        .values_list("bybit_symbol", flat=True)
    )

    for sig in reps:
        if sig.id in acted_signal_ids:
            continue
        if open_positions >= max_open:
            break  # position budget full — stop, don't skip-record every remaining
        if trades_today >= max_daily:
            break  # daily cap reached
        # Per-signal freshness (tighter than the query lookback).
        if (now - sig.generated_at).total_seconds() > max_age:
            continue
        bybit_symbol = _bybit_symbol(sig.symbol)
        if bybit_symbol is None:
            continue  # forex / untradeable
        if bybit_symbol in open_symbols:
            continue  # already hold this symbol — don't net a second position onto it

        outcome = _place_one(client, cred, sig, bybit_symbol, now)
        counts[outcome] = counts.get(outcome, 0) + 1
        acted_signal_ids.add(sig.id)
        if outcome in ("placed", "deduped"):
            open_positions += 1
            trades_today += 1
            open_symbols.add(bybit_symbol)

    return counts


def run_auto_trades() -> dict:
    """Place auto-trades for every enabled, premium user. No-op unless the feature
    is globally on and the encryption key is configured."""
    if not settings.AUTO_TRADE_ENABLED:
        return {"skipped": "AUTO_TRADE_ENABLED is off"}
    if not crypto.is_configured():
        logger.error("auto-trade: BROKER_ENCRYPTION_KEY not set — cannot read credentials")
        return {"skipped": "no encryption key"}

    now = timezone.now()
    totals = {"users": 0, "placed": 0, "skipped": 0, "rejected": 0, "deduped": 0}
    creds = (
        BrokerCredential.objects.filter(enabled=True)
        .exclude(api_key_enc="")
        .exclude(api_secret_enc="")
        .select_related("user")
    )
    for cred in creds:
        # Premium-gated, expiry-aware — mirrors Telegram delivery. An expired plan
        # silently stops auto-trading; it resumes on resubscribe with no re-linking.
        if not cred.user.is_premium:
            continue
        totals["users"] += 1
        try:
            counts = _run_for_user(cred, now)
        except Exception:  # never let one user's failure abort the batch
            logger.exception("auto-trade failed for user %s", cred.user_id)
            continue
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v

    if totals["placed"] or totals["rejected"]:
        logger.info(
            "auto-trade run: users=%(users)d placed=%(placed)d skipped=%(skipped)d "
            "rejected=%(rejected)d deduped=%(deduped)d", totals,
        )
    return totals


# --- reconciliation --------------------------------------------------------


def _move_to_breakeven(client, row) -> bool:
    """A scale-out trade whose position has shrunk since entry has had a take-profit
    rung fill — trail its stop to the entry price so the remaining runner can't turn
    into a loss (§19.2). Returns True if the stop was moved."""
    try:
        client.set_stop_loss(row.bybit_symbol, row.entry_price)
    except BybitError as exc:
        logger.warning("reconcile: breakeven move failed %s: %s", row.bybit_symbol, exc)
        return False
    row.breakeven_moved = True
    row.save(update_fields=["breakeven_moved", "updated_at"])
    return True


def _cancel_rungs(client, row) -> None:
    """Cancel any still-resting take-profit rungs once the position is closed. The
    rung link ids are deterministic from the entry's (stored) order_link_id, so no
    per-rung id needs persisting. Missing rungs (already filled) are a no-op."""
    if not row.order_link_id:
        return
    for n in (1, 2, 3):
        try:
            client.cancel_order(row.bybit_symbol, f"{row.order_link_id}-tp{n}")
        except BybitError as exc:
            logger.warning("reconcile: cancel rung tp%d failed %s: %s", n, row.bybit_symbol, exc)


def run_reconcile() -> dict:
    """Poll Bybit for each user's open positions and manage their lifecycle:

    - A scale-out position that has SHRUNK below its entry size (a TP rung filled) has
      its stop trailed to breakeven, once.
    - A position that is GONE (TP/SL hit, liquidation, or manual close) flips the row
      out of OPEN and cancels any resting TP rungs.

    Best-effort and cheap — one positions call per user with open rows. Bybit doesn't
    retain a closed position in the live list, so 'no longer present' is the close
    signal; a liquidation is inferred when the last price crossed the recorded liq
    price. Realised P&L is left null unless a later closed-PnL lookup is added — the
    row still leaves OPEN so the position/daily budgets free up.
    """
    if not settings.AUTO_TRADE_ENABLED or not crypto.is_configured():
        return {"skipped": "disabled"}

    now = timezone.now()
    closed = liquidated = moved_be = checked = 0
    user_ids = (
        TradeExecution.objects.filter(status=TradeExecution.Status.OPEN)
        .values_list("user_id", flat=True).distinct()
    )
    for uid in list(user_ids):
        cred = BrokerCredential.objects.filter(user_id=uid).select_related("user").first()
        if not cred or not cred.has_keys:
            continue
        try:
            client = BybitClient(cred.api_key, cred.api_secret, testnet=cred.testnet)
            positions = client.get_positions()
        except (BybitError, crypto.BrokerCryptoError) as exc:
            logger.warning("reconcile: positions fetch failed for user %s: %s", uid, exc)
            continue
        # symbol -> live position size (0.0 if flat / not present).
        sizes = {p["symbol"]: float(p.get("size", 0) or 0) for p in positions}
        open_rows = TradeExecution.objects.filter(
            user_id=uid, status=TradeExecution.Status.OPEN
        )
        for row in open_rows:
            checked += 1
            size = sizes.get(row.bybit_symbol, 0.0)
            if size != 0:
                # Still open. Trail to breakeven the first time a scale-out rung fills
                # (size drops below the recorded entry qty, allowing for step rounding).
                if (row.scaleout and not row.breakeven_moved
                        and row.qty and size < row.qty * 0.999):
                    if _move_to_breakeven(client, row):
                        moved_be += 1
                continue
            # Position gone. Infer liquidation if price sits on the wrong side of the
            # recorded liq price; otherwise treat as a normal close.
            status = TradeExecution.Status.CLOSED
            try:
                last = client.get_last_price(row.bybit_symbol)
                if row.liq_price:
                    is_long = row.direction == Signal.Direction.BUY
                    if (is_long and last <= row.liq_price) or (
                        not is_long and last >= row.liq_price
                    ):
                        status = TradeExecution.Status.LIQUIDATED
            except BybitError:
                pass
            _cancel_rungs(client, row)
            row.status = status
            row.closed_at = now
            row.save(update_fields=["status", "closed_at", "updated_at"])
            if status == TradeExecution.Status.LIQUIDATED:
                liquidated += 1
            else:
                closed += 1

    if closed or liquidated or moved_be:
        logger.info("reconcile: closed=%d liquidated=%d breakeven=%d checked=%d",
                    closed, liquidated, moved_be, checked)
    return {"closed": closed, "liquidated": liquidated,
            "breakeven_moved": moved_be, "checked": checked}
