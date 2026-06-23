"""Auto-trade execution engine (Phase 2).

Event-driven: when the signal scan writes a new Signal, it enqueues
`execute_signal_for_subscribers(signal_id)` (see signals/tasks.py). For each Pro
user who follows that strategy, watches that symbol, and has auto-trade enabled
with a verified broker, this places the trade on their own Bybit account using
the signal's parameters and their risk config.

Idempotent: one TradeExecution per (user, signal) — the unique constraint means a
duplicate task can never double-place. Nothing runs unless settings.
AUTO_TRADE_ENABLED is on (global kill switch). TESTNET-first by connection.

`run_execution()` is plain Python so it can be driven synchronously in tests /
management commands without a worker.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.signals.models import Signal, UserSignalSubscription
from apps.watchlists.models import WatchlistItem

from collections import defaultdict

from .brokers import BrokerError, get_client
from .models import AutoTradeConfig, TradeExecution
from .sizing import SizingError, compute_qty, filled_tp_legs, split_take_profits

logger = logging.getLogger("auto_trade.tasks")


def enqueue_execution(signal_id: int) -> None:
    """Fire-and-forget hook called right after a Signal is created. Cheap no-op
    unless the feature is globally enabled, so the signal scan pays nothing while
    auto-trade is off."""
    if not settings.AUTO_TRADE_ENABLED:
        return
    try:
        execute_signal_for_subscribers.delay(signal_id)
    except Exception:  # broker/queue down — never let this break signal generation
        logger.exception("failed to enqueue execution for signal %s", signal_id)


def _eligible_configs(signal: Signal):
    """Configs for users who both follow the signal's strategy and watch its
    symbol, and have auto-trade enabled."""
    follower_ids = UserSignalSubscription.objects.filter(
        service_id=signal.service_id
    ).values_list("user_id", flat=True)
    watcher_ids = WatchlistItem.objects.filter(
        symbol_id=signal.symbol_id
    ).values_list("user_id", flat=True)
    return (
        AutoTradeConfig.objects.filter(
            enabled=True, user_id__in=follower_ids
        )
        .filter(user_id__in=watcher_ids)
        .select_related("user")
    )


def _record(user, signal, conn, status, detail="", **extra) -> tuple[TradeExecution, bool]:
    """Create the (user, signal) execution row, honoring the unique constraint.
    Returns (row, created); created=False means another run already handled it."""
    try:
        with transaction.atomic():
            return (
                TradeExecution.objects.create(
                    user=user, signal=signal, broker_connection=conn,
                    status=status, detail=detail, **extra,
                ),
                True,
            )
    except IntegrityError:
        return TradeExecution.objects.filter(user=user, signal=signal).first(), False


def _execute_for_user(signal: Signal, config: AutoTradeConfig) -> str:
    """Place the trade for one user. Returns a short status string for the summary."""
    user = config.user

    # Pro-only, expiry-aware.
    if user.plan_key != "pro":
        return "not_pro"
    conn = getattr(user, "broker_connection", None)
    if conn is None or not conn.is_usable:
        return "no_broker"

    # Already handled? (idempotency — claim the slot up front as SUBMITTED.)
    if TradeExecution.objects.filter(user=user, signal=signal).exists():
        return "dup"

    bybit_symbol = signal.symbol.bybit_symbol
    if not bybit_symbol:
        _record(user, signal, conn, TradeExecution.Status.SKIPPED, "symbol not mapped to Bybit")
        return "unmapped"

    # Confidence floor (user override or global).
    min_conf = config.min_confidence or settings.SIGNAL_MIN_CONFIDENCE
    if signal.confidence_pct < min_conf:
        return "low_confidence"

    # Daily and concurrent caps.
    today = timezone.now().date()
    placed_today = TradeExecution.objects.filter(
        user=user, created_at__date=today,
        status__in=[TradeExecution.Status.SUBMITTED, TradeExecution.Status.OPEN,
                    TradeExecution.Status.CLOSED],
    ).count()
    if placed_today >= config.max_daily_trades:
        _record(user, signal, conn, TradeExecution.Status.SKIPPED, "daily trade cap reached")
        return "daily_cap"
    open_count = TradeExecution.objects.filter(
        user=user, status=TradeExecution.Status.OPEN
    ).count()
    if open_count >= config.max_open_positions:
        _record(user, signal, conn, TradeExecution.Status.SKIPPED, "max open positions reached")
        return "position_cap"

    side = "Buy" if signal.direction == Signal.Direction.BUY else "Sell"

    try:
        client = get_client(conn)
        live = client.get_last_price(bybit_symbol)
    except BrokerError as exc:
        _record(user, signal, conn, TradeExecution.Status.ERROR, str(exc)[:300])
        return "broker_error"

    # Slippage guard: skip if price has already run past the signal entry.
    entry = signal.entry_price
    if entry > 0 and abs(live - entry) / entry * 100.0 > config.max_slippage_pct:
        _record(user, signal, conn, TradeExecution.Status.SKIPPED,
                f"slippage {abs(live-entry)/entry*100:.2f}% > {config.max_slippage_pct}%",
                side=side, bybit_symbol=bybit_symbol, intended_entry=entry)
        return "slippage"

    # Size the position (1%-risk by default).
    try:
        equity = client.get_equity_usd()
        qty = compute_qty(equity, entry, signal.stop_loss, config)
    except (BrokerError, SizingError) as exc:
        _record(user, signal, conn, TradeExecution.Status.ERROR, str(exc)[:300])
        return "sizing_error"

    # Claim the (user, signal) slot before sending the order so a concurrent run
    # can't place a second trade.
    row, created = _record(
        user, signal, conn, TradeExecution.Status.SUBMITTED,
        side=side, bybit_symbol=bybit_symbol, qty=qty,
        leverage=config.leverage, intended_entry=entry,
    )
    if not created:
        return "dup"

    try:
        entry_res = client.place_market_entry(bybit_symbol, side, qty, config.leverage)
    except BrokerError as exc:
        row.status = TradeExecution.Status.REJECTED
        row.detail = str(exc)[:300]
        row.save(update_fields=["status", "detail", "updated_at"])
        return "rejected"

    filled_qty = entry_res.get("qty", qty)
    tp_legs = split_take_profits(
        filled_qty, [signal.tp1, signal.tp2, signal.tp3, signal.tp4], config.tp_distribution
    )
    try:
        protective = client.place_protective_orders(
            bybit_symbol, side, filled_qty, signal.stop_loss, tp_legs
        )
    except BrokerError as exc:
        # Entry filled but protection failed — DANGEROUS (unprotected position).
        # Flatten immediately rather than leave it naked.
        logger.error("protective orders failed for %s; closing position: %s", bybit_symbol, exc)
        try:
            client.close_position(bybit_symbol)
        except BrokerError:
            logger.exception("emergency close ALSO failed for %s — needs manual attention", bybit_symbol)
        row.status = TradeExecution.Status.ERROR
        row.detail = f"protection failed, position closed: {str(exc)[:240]}"
        row.order_ids = {"entry": entry_res.get("order_id", "")}
        row.save(update_fields=["status", "detail", "order_ids", "updated_at"])
        return "protection_failed"

    row.status = TradeExecution.Status.OPEN
    row.qty = filled_qty
    row.fill_price = entry_res.get("avg_price")
    row.order_ids = {"entry": entry_res.get("order_id", ""), **protective}
    row.save(update_fields=["status", "qty", "fill_price", "order_ids", "updated_at"])
    return "placed"


def run_execution(signal_id: int) -> dict:
    """Place trades for all eligible users on a single signal."""
    if not settings.AUTO_TRADE_ENABLED:
        return {"skipped": "AUTO_TRADE_ENABLED is off"}

    try:
        signal = Signal.objects.select_related("symbol", "service").get(id=signal_id)
    except Signal.DoesNotExist:
        return {"error": "signal not found"}

    # Only act on live directional calls.
    if signal.direction not in (Signal.Direction.BUY, Signal.Direction.SELL):
        return {"skipped": "non-directional"}
    if signal.outcome != Signal.Outcome.PENDING:
        return {"skipped": "signal not pending"}

    age = (timezone.now() - signal.generated_at).total_seconds()

    placed = 0
    outcomes: dict[str, int] = {}
    for config in _eligible_configs(signal):
        # Per-user freshness gate (each user can set their own tolerance).
        if age > config.max_signal_age_sec:
            outcomes["stale"] = outcomes.get("stale", 0) + 1
            continue
        try:
            result = _execute_for_user(signal, config)
        except Exception:
            logger.exception("execution failed for user %s on signal %s", config.user_id, signal_id)
            result = "exception"
        outcomes[result] = outcomes.get(result, 0) + 1
        if result == "placed":
            placed += 1

    summary = {"signal": signal_id, "placed": placed, "outcomes": outcomes}
    logger.info("auto-trade execution: signal=%s placed=%d %s", signal_id, placed, outcomes)
    return summary


@shared_task(name="apps.auto_trade.tasks.execute_signal_for_subscribers")
def execute_signal_for_subscribers(signal_id: int):
    return run_execution(signal_id)


# --- Phase 3: reconciliation + kill-switch flatten -------------------------
#
# The execution engine above opens positions but nothing closes the TradeExecution
# row — once the exchange fills a TP/SL the position is flat there but still OPEN
# in our DB. The reconciler polls the broker for every OPEN row, records the
# outcome when a position has closed, and moves the stop to break-even once the
# configured number of take-profits have filled. It also backs the panic kill
# switch, which flattens live positions immediately.


def _infer_close_reason(execution: TradeExecution, pnl: float | None) -> str:
    """Best-effort close reason for a position the exchange already flattened.

    We can't always tell SL from TP after the fact (partial scale-outs muddy a
    single PnL number), so this is a heuristic: profit ⇒ a TP carried it, loss ⇒
    the stop hit. Left blank when PnL is unknown rather than guessed."""
    if pnl is None:
        return ""
    return TradeExecution.CloseReason.TP if pnl >= 0 else TradeExecution.CloseReason.SL


def _maybe_move_to_breakeven(client, execution: TradeExecution, pos: dict) -> bool:
    """Once `move_sl_to_be_after_tp` take-profits have filled, amend the stop to
    the entry price so the trade can't turn back into a loss. Idempotent via
    sl_moved_to_be. Returns True if the stop was moved this pass."""
    if execution.sl_moved_to_be:
        return False
    cfg = getattr(execution.user, "auto_trade_config", None)
    after_n = cfg.move_sl_to_be_after_tp if cfg else 0
    if not after_n or not execution.qty:
        return False

    remaining_frac = pos["size"] / execution.qty
    filled_frac = max(0.0, 1.0 - remaining_frac)
    legs = filled_tp_legs(filled_frac, cfg.tp_distribution or [25, 25, 25, 25])
    if legs < after_n:
        return False

    be_price = execution.fill_price or execution.intended_entry
    sl_id = (execution.order_ids or {}).get("sl")
    if not be_price or not sl_id:
        return False
    moved = client.amend_stop_trigger(execution.bybit_symbol, sl_id, be_price)
    if moved:
        execution.sl_moved_to_be = True
        logger.info("moved SL to break-even for execution %s (%s)", execution.id, execution.bybit_symbol)
    return moved


def _reconcile_execution(client, execution: TradeExecution) -> str:
    """Reconcile one OPEN execution against the exchange. Returns a status word."""
    now = timezone.now()
    pos = client.get_position(execution.bybit_symbol)

    if pos is None:
        # Flat on the exchange → the trade has closed (TP/SL/external). Record it.
        since_ms = int(execution.created_at.timestamp() * 1000)
        try:
            pnl = client.get_realized_pnl(execution.bybit_symbol, since_ms)
        except BrokerError:
            logger.warning("could not read realized pnl for execution %s", execution.id)
            pnl = None
        execution.status = TradeExecution.Status.CLOSED
        execution.realized_pnl = pnl
        execution.close_reason = execution.close_reason or _infer_close_reason(execution, pnl)
        execution.closed_at = now
        execution.last_reconciled_at = now
        execution.save(update_fields=[
            "status", "realized_pnl", "close_reason", "closed_at",
            "last_reconciled_at", "updated_at",
        ])
        return "closed"

    moved = _maybe_move_to_breakeven(client, execution, pos)
    execution.last_reconciled_at = now
    fields = ["last_reconciled_at", "updated_at"]
    if moved:
        fields.append("sl_moved_to_be")
    execution.save(update_fields=fields)
    return "be_moved" if moved else "still_open"


def run_reconcile() -> dict:
    """Reconcile every OPEN execution against its broker. Plain Python so it can
    be driven from a management command / test without a worker."""
    if not settings.AUTO_TRADE_ENABLED:
        return {"skipped": "AUTO_TRADE_ENABLED is off"}

    open_execs = list(
        TradeExecution.objects.filter(status=TradeExecution.Status.OPEN)
        .select_related("user", "user__auto_trade_config", "user__broker_connection")
    )
    by_user: dict[int, list[TradeExecution]] = defaultdict(list)
    for ex in open_execs:
        by_user[ex.user_id].append(ex)

    outcomes: dict[str, int] = {}
    for execs in by_user.values():
        conn = getattr(execs[0].user, "broker_connection", None)
        if conn is None or not conn.is_usable:
            outcomes["no_broker"] = outcomes.get("no_broker", 0) + len(execs)
            continue
        try:
            client = get_client(conn)
        except BrokerError:
            logger.exception("could not build client for user %s", execs[0].user_id)
            outcomes["client_error"] = outcomes.get("client_error", 0) + len(execs)
            continue
        for ex in execs:
            try:
                result = _reconcile_execution(client, ex)
            except BrokerError as exc:
                logger.warning("reconcile failed for execution %s: %s", ex.id, exc)
                result = "error"
            except Exception:
                logger.exception("reconcile crashed for execution %s", ex.id)
                result = "exception"
            outcomes[result] = outcomes.get(result, 0) + 1

    summary = {"open": len(open_execs), "outcomes": outcomes}
    logger.info("auto-trade reconcile: %s", summary)
    return summary


def _close_execution(client, execution: TradeExecution, reason: str) -> bool:
    """Market-close one OPEN position, cancel its leftover protective orders, and
    record the outcome. Returns True on success. Raises BrokerError if the close
    itself fails (caller decides whether to keep going)."""
    client.close_position(execution.bybit_symbol)
    client.cancel_all_orders(execution.bybit_symbol)
    try:
        execution.realized_pnl = client.get_realized_pnl(
            execution.bybit_symbol, int(execution.created_at.timestamp() * 1000)
        )
    except BrokerError:
        pass  # PnL is best-effort; the close already happened
    execution.status = TradeExecution.Status.CLOSED
    execution.close_reason = reason
    execution.closed_at = timezone.now()
    execution.save(update_fields=[
        "status", "close_reason", "realized_pnl", "closed_at", "updated_at",
    ])
    return True


def _close_executions(execs: list[TradeExecution], reason: str) -> dict:
    """Flatten a batch of OPEN executions, grouping one broker client per user.
    Best-effort per position: one failure doesn't abort the rest."""
    by_user: dict[int, list[TradeExecution]] = defaultdict(list)
    for ex in execs:
        by_user[ex.user_id].append(ex)

    closed = errors = 0
    for user_execs in by_user.values():
        conn = getattr(user_execs[0].user, "broker_connection", None)
        if conn is None or not conn.is_usable:
            errors += len(user_execs)
            continue
        try:
            client = get_client(conn)
        except BrokerError:
            logger.exception("could not build client for user %s", user_execs[0].user_id)
            errors += len(user_execs)
            continue
        for ex in user_execs:
            try:
                _close_execution(client, ex, reason)
                closed += 1
            except BrokerError:
                logger.exception("close failed for execution %s", ex.id)
                errors += 1

    result = {"closed": closed}
    if errors:
        result["errors"] = errors
    return result


def flatten_open_trades(user, reason: str = TradeExecution.CloseReason.MANUAL) -> dict:
    """Market-close every OPEN position for a user. Backs the panic kill switch."""
    execs = list(
        TradeExecution.objects.filter(user=user, status=TradeExecution.Status.OPEN)
        .select_related("user", "user__broker_connection")
    )
    if not execs:
        return {"closed": 0}
    return _close_executions(execs, reason)


def run_invalidation_close(signal_ids) -> dict:
    """Close open positions whose signal was just invalidated (trend flipped).

    The reconciler only acts when the EXCHANGE has closed a position (TP/SL hit);
    this handles the other case — the thesis broke before either level was reached,
    so we exit proactively rather than ride a stale call. No-op unless the feature
    is live."""
    if not settings.AUTO_TRADE_ENABLED or not signal_ids:
        return {"closed": 0}
    execs = list(
        TradeExecution.objects.filter(
            signal_id__in=signal_ids, status=TradeExecution.Status.OPEN
        ).select_related("user", "user__broker_connection")
    )
    if not execs:
        return {"closed": 0}
    result = _close_executions(execs, TradeExecution.CloseReason.INVALIDATED)
    logger.info("auto-trade invalidation close: signals=%s %s", list(signal_ids), result)
    return result


def enqueue_invalidation_close(signal_ids) -> None:
    """Fire-and-forget hook from the signal scan when calls are invalidated. Cheap
    no-op while the feature is off, so the scan pays nothing for it."""
    if not settings.AUTO_TRADE_ENABLED or not signal_ids:
        return
    try:
        close_invalidated_signals.delay(list(signal_ids))
    except Exception:  # queue down — never let this break signal generation
        logger.exception("failed to enqueue invalidation close for %s", signal_ids)


@shared_task(name="apps.auto_trade.tasks.reconcile_open_trades")
def reconcile_open_trades():
    return run_reconcile()


@shared_task(name="apps.auto_trade.tasks.close_invalidated_signals")
def close_invalidated_signals(signal_ids):
    return run_invalidation_close(signal_ids)
