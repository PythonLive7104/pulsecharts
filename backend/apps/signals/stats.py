"""Realized accuracy stats (Section 18, 20.5).

Aggregates resolved Signal outcomes into honest win-rate metrics. A "win" is any
signal that reached at least TP1 before being stopped out; a "loss" is a stop hit
with no TP. A trend-flip invalidation is a breakeven close (0% P/L) — counted as
neither a win nor a loss. EXPIRED, PENDING and breakeven are reported separately
so the win rate isn't flattered or penalised by them.
"""

from django.db.models import Count

from .models import Signal

TP_OUTCOMES = {"TP1", "TP2", "TP3", "TP4"}


def _summarize(counts: dict) -> dict:
    wins = sum(counts.get(o, 0) for o in TP_OUTCOMES)
    losses = counts.get("SL", 0)
    # Trend-flip invalidations close flat — neither win nor loss. Kept out of the
    # win-rate denominator so a breakeven exit never reads as a stopped-out trade.
    breakeven = counts.get("INVALID", 0)
    resolved = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "expired": counts.get("EXPIRED", 0),
        "pending": counts.get("PENDING", 0),
        "resolved": resolved,
        "win_rate": round(wins / resolved * 100, 1) if resolved else None,
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
        "note": "Win = reached TP1+ before stop. Breakeven (trend flipped), "
                "expired and pending are excluded from win rate.",
    }
