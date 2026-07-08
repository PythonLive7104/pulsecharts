"""Realized accuracy stats (Section 18, 20.5).

Aggregates resolved Signal outcomes into honest win-rate metrics. A "win" is any
signal that reached at least TP1 before being stopped out; a "loss" is a stop hit
with no TP. A trend-flip invalidation closes the call flat at 0 P/L — counted as
neither a win nor a loss. EXPIRED, PENDING and invalidated are reported separately
so the win rate isn't flattered or penalised by them.
"""

from django.db.models import Count

from .models import Signal

TP_OUTCOMES = {"TP1", "TP2", "TP3", "TP4"}

# Realized R per outcome under the live scale-out-in-thirds model (§19.2): bank 1/3
# at each target, stop trails to breakeven after TP1, so the unfilled remainder
# closes flat. TP1 = (1×1R + 2×0)/3, TP2 = (1+2+0)/3, TP3 = (1+2+3)/3. A stop hit
# before any TP loses the full 1R; a trend-flip invalidation closes flat at 0.
SCALEOUT_R = {"TP1": 1 / 3, "TP2": 1.0, "TP3": 2.0, "TP4": 3.0, "SL": -1.0, "INVALID": 0.0}


def _summarize(counts: dict) -> dict:
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
        "win_rate": round(wins / resolved * 100, 1) if resolved else None,
        "avg_r": round(total_r / closed, 3) if closed else None,
        "by_outcome": {o: counts.get(o, 0) for o in
                       ["TP1", "TP2", "TP3", "TP4", "SL", "INVALID", "EXPIRED", "PENDING"]},
    }


def accuracy_stats() -> dict:
    """Overall + per-strategy realized accuracy."""
    overall_counts = dict(
        Signal.objects.values_list("outcome").annotate(n=Count("id")).values_list("outcome", "n")
    )

    per_service = {}
    rows = (
        Signal.objects.values("service__slug", "service__name", "outcome")
        .annotate(n=Count("id"))
    )
    for r in rows:
        slug = r["service__slug"]
        bucket = per_service.setdefault(slug, {"name": r["service__name"], "_counts": {}})
        bucket["_counts"][r["outcome"]] = r["n"]

    strategies = []
    for slug, b in per_service.items():
        strategies.append({"slug": slug, "name": b["name"], **_summarize(b["_counts"])})
    strategies.sort(key=lambda s: (s["win_rate"] is None, -(s["win_rate"] or 0)))

    return {
        "overall": _summarize(overall_counts),
        "strategies": strategies,
        "note": "Win = reached TP1+ before stop. Invalidated (trend flipped), "
                "expired and pending are excluded from win rate. avg_r = per-trade "
                "expectancy under the scale-out-in-thirds model (TP1=+0.33R, TP2=+1R, "
                "TP3=+2R, SL=-1R, trend-flip=0R).",
    }
