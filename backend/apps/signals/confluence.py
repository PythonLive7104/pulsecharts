"""Delivery-side confluence collapse (Option A).

The scan generates one Signal per (symbol, service, timeframe), so a single coin
can surface several cards at once — one per strategy. Confluence collapses those
to a single, higher-conviction signal per (symbol, timeframe): pick the direction
the most distinct strategies agree on, and surface it only when at least
``settings.SIGNAL_CONFLUENCE_MIN`` of them concur. The highest-confidence agreeing
call is the representative shown, annotated with how many — and which — strategies
agree (``.confluence_count`` / ``.confluence_services``).

This is purely a *delivery* filter: it reads already-generated Signal rows and
never changes what the engine stores, so it's fully reversible via the setting.
Inputs must have ``service`` loaded (use ``select_related("service")``).
"""

from __future__ import annotations

from collections import defaultdict

from django.conf import settings

from .models import Signal


def confluence_min() -> int:
    """Minimum distinct agreeing strategies to surface a signal (>= 1)."""
    return max(1, int(getattr(settings, "SIGNAL_CONFLUENCE_MIN", 1)))


def _group(signals) -> dict:
    """(symbol_id, timeframe) -> {direction: {service_id: best signal for that service}}.

    Keeps only the highest-confidence signal per service per direction, so a
    strategy that somehow fired twice still counts as one vote.
    """
    groups: dict = defaultdict(lambda: defaultdict(dict))
    for s in signals:
        svc_map = groups[(s.symbol_id, s.timeframe)][s.direction]
        cur = svc_map.get(s.service_id)
        if cur is None or s.confidence_pct > cur.confidence_pct:
            svc_map[s.service_id] = s
    return groups


def _winning_direction(by_dir: dict):
    """Direction the most distinct services agree on (tie broken by summed
    confidence), or None if empty."""
    best_dir, best_score = None, None
    for direction, svc_map in by_dir.items():
        score = (len(svc_map), sum(s.confidence_pct for s in svc_map.values()))
        if best_score is None or score > best_score:
            best_dir, best_score = direction, score
    return best_dir


def _annotate(signal: Signal, svc_map: dict) -> Signal:
    signal.confluence_count = len(svc_map)
    signal.confluence_services = sorted(s.service.name for s in svc_map.values())
    return signal


def collapse(signals) -> list[Signal]:
    """Collapse candidate signals to one representative per (symbol, timeframe)
    that meets the confluence threshold. Each representative is annotated with
    ``.confluence_count`` / ``.confluence_services``. Returned newest-first.
    """
    k = confluence_min()
    reps: list[Signal] = []
    for by_dir in _group(signals).values():
        direction = _winning_direction(by_dir)
        if direction is None:
            continue
        svc_map = by_dir[direction]
        if len(svc_map) < k:
            continue
        rep = max(svc_map.values(), key=lambda s: s.confidence_pct)
        reps.append(_annotate(rep, svc_map))
    reps.sort(key=lambda s: s.generated_at, reverse=True)
    return reps


def annotate(signals, pool) -> list:
    """Attach confluence metadata to already-chosen ``signals`` (e.g. the active
    feed of previously-delivered representatives) by counting agreement among
    ``pool`` (the sibling candidates within the lookback window). The signal's own
    strategy is always counted, even if it has aged out of the pool. Mutates and
    returns ``signals``.
    """
    groups = _group(pool)
    for s in signals:
        svc_map = dict(groups.get((s.symbol_id, s.timeframe), {}).get(s.direction, {}))
        svc_map.setdefault(s.service_id, s)  # ensure self is counted
        _annotate(s, svc_map)
    return signals
