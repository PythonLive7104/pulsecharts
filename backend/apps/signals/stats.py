"""Realized accuracy stats (Section 18, 20.5).

Aggregates resolved Signal outcomes into honest win-rate metrics. A "win" is any
signal that reached at least TP1 before being stopped out; a "loss" is a stop hit
with no TP. A trend-flip invalidation closes the call flat at 0 P/L — counted as
neither a win nor a loss. EXPIRED, PENDING and invalidated are reported separately
so the win rate isn't flattered or penalised by them.

The overall figure counts TRADES, not Signal rows. The scan writes one row per
strategy, so a setup that six strategies agreed on becomes six identical rows that
resolve together — while the user was delivered a single confluence-collapsed card.
Counting rows made one stopped-out trade read as six losses and weighted the win
rate by how many strategies happened to agree. Rows are deduped on
(symbol, timeframe, direction, entry_price) — the same trade grain the "Past
results" panel uses — so each trade counts once.

The caller passes the ``base`` queryset that defines scope. The signals page scopes
it to the SAME filter as "Past results" (the user's followed strategies, watched
symbols, and results lookback window), so the accuracy headline and the results
list always reconcile. Because a user can only ever follow their own custom
strategies (they auto-subscribe the owner and aren't followable by anyone else),
scoping by followed strategies also keeps one user's private custom strategy out of
another user's stats — no separate owner filter is needed.
"""

from django.db.models import Count

from .models import Signal

TP_OUTCOMES = {"TP1", "TP2", "TP3", "TP4"}

# Realized R per outcome under the live scale-out-in-thirds model (§19.2): bank 1/3
# at each target, stop trails to breakeven after TP1, so the unfilled remainder
# closes flat. TP1 = (1×1R + 2×0)/3, TP2 = (1+2+0)/3, TP3 = (1+2+3)/3. A stop hit
# before any TP loses the full 1R; a trend-flip invalidation closes flat at 0.
SCALEOUT_R = {"TP1": 1 / 3, "TP2": 1.0, "TP3": 2.0, "TP4": 3.0, "SL": -1.0, "INVALID": 0.0}


def _effective_outcome(outcome: str, best_tp: int) -> str:
    """Outcome for stats purposes, counting still-open trades that already banked a
    target.

    A PENDING trade that has tagged TP1+ is NOT undecided: a third is banked and the
    stop is at breakeven (§19.2), so its floor is exactly the R of the TP it reached
    (TP1 → 1/3 R: one third at 1R, the rest flat) and it can no longer become a loss.
    Excluding it while counting every stop-out biases the whole figure downward,
    because losers resolve immediately and winners stay open for hours chasing TP3 —
    the sample would hold nearly all the losses and only the finished wins.

    Counting it at its floor is the conservative correction: never better than what
    is already locked in, and the upside (a runner reaching TP3) only lands later.
    """
    if outcome == "PENDING" and best_tp:
        return f"TP{min(best_tp, 4)}"
    return outcome


def _summarize(counts: dict, running: int = 0) -> dict:
    wins = sum(counts.get(o, 0) for o in TP_OUTCOMES)
    losses = counts.get("SL", 0)
    # Trend-flip invalidations close flat — neither win nor loss. Kept out of the
    # win-rate denominator so a breakeven exit never reads as a stopped-out trade.
    breakeven = counts.get("INVALID", 0)
    resolved = wins + losses
    # Per-trade expectancy in R, over every closed trade that committed capital
    # (wins + losses + flat trend-flip closes; pending/expired excluded).
    closed = resolved + breakeven
    total_r = sum(SCALEOUT_R.get(o, 0.0) * n for o, n in counts.items())
    return {
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "expired": counts.get("EXPIRED", 0),
        "pending": counts.get("PENDING", 0),
        "resolved": resolved,
        # How many of the counted trades are still open with a target already banked
        # (counted at their locked-in floor — see _effective_outcome). Surfaced so the
        # UI can say so out loud rather than quietly mixing open trades into a figure
        # labelled "realized".
        "running": running,
        "win_rate": round(wins / resolved * 100, 1) if resolved else None,
        "avg_r": round(total_r / closed, 3) if closed else None,
        "by_outcome": {o: counts.get(o, 0) for o in
                       ["TP1", "TP2", "TP3", "TP4", "SL", "INVALID", "EXPIRED", "PENDING"]},
    }


def _trade_counts(qs) -> dict:
    """Outcome counts over distinct TRADES rather than Signal rows.

    A trade is (symbol, timeframe, direction, entry_price): every strategy that
    called the same setup shares those, and they resolve together. A later, distinct
    call on the same pair has a different entry, so it stays its own trade. Where
    rows for one trade somehow disagree, the worst outcome wins — never report a
    trade as a win because one of its rows was more optimistic.
    """
    rank = {"SL": 0, "EXPIRED": 1, "INVALID": 2, "PENDING": 3,
            "TP1": 4, "TP2": 5, "TP3": 6, "TP4": 7}
    trades: dict[tuple, tuple[str, bool]] = {}
    for r in qs.values("symbol_id", "timeframe", "direction", "entry_price",
                       "outcome", "best_tp"):
        key = (r["symbol_id"], r["timeframe"], r["direction"], r["entry_price"])
        outcome = _effective_outcome(r["outcome"], r["best_tp"])
        still_open = r["outcome"] == "PENDING"
        cur = trades.get(key)
        if cur is None or rank.get(outcome, 9) < rank.get(cur[0], 9):
            trades[key] = (outcome, still_open)
    counts: dict[str, int] = {}
    running = 0
    for outcome, still_open in trades.values():
        counts[outcome] = counts.get(outcome, 0) + 1
        if still_open and outcome in TP_OUTCOMES:
            running += 1
    return counts, running


def accuracy_stats(base=None) -> dict:
    """Overall + per-strategy realized accuracy over ``base``.

    ``base`` is the queryset defining scope (see module docstring). Defaults to every
    resolved built-in signal, all-time — the widest honest sample — for callers that
    want a product-wide figure or pass no user context. ``overall`` is trade-level
    (deduped); ``strategies`` is per-strategy, which is already one row per trade
    since the scan writes at most one signal per symbol/strategy/timeframe.
    """
    if base is None:
        base = Signal.objects.filter(service__owner__isnull=True)
    overall_counts, overall_running = _trade_counts(base)

    # Per-strategy: same effective-outcome mapping as the overall figure, so an open
    # trade that banked TP1 lands in both or neither. (Rolled up in Python rather than
    # via annotate() because the mapping depends on best_tp, not just outcome.)
    per_service: dict[str, dict] = {}
    rows = base.values("service__slug", "service__name", "outcome", "best_tp").annotate(
        n=Count("id")
    )
    for r in rows:
        slug = r["service__slug"]
        bucket = per_service.setdefault(
            slug, {"name": r["service__name"], "_counts": {}, "_running": 0}
        )
        outcome = _effective_outcome(r["outcome"], r["best_tp"])
        bucket["_counts"][outcome] = bucket["_counts"].get(outcome, 0) + r["n"]
        if r["outcome"] == "PENDING" and outcome in TP_OUTCOMES:
            bucket["_running"] += r["n"]

    strategies = []
    for slug, b in per_service.items():
        strategies.append(
            {"slug": slug, "name": b["name"], **_summarize(b["_counts"], b["_running"])}
        )
    strategies.sort(key=lambda s: (s["win_rate"] is None, -(s["win_rate"] or 0)))

    return {
        "overall": _summarize(overall_counts, overall_running),
        "strategies": strategies,
        "note": "Win = reached TP1+ before stop. Closed trades only — a trade still "
                "running is not a result yet, however well it's doing. Invalidated "
                "(trend flipped), expired and open trades are excluded from the win "
                "rate. Counted once per "
                "trade, not once per strategy that called it. avg_r = per-trade "
                "expectancy under the scale-out-in-thirds model (TP1=+0.33R, TP2=+1R, "
                "TP3=+2R, SL=-1R, trend-flip=0R).",
    }
