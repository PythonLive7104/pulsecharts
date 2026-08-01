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
from django.db.models import Q

from .models import Signal
from .pregate import kind_of


def deliverable_q() -> Q:
    """Filter for the delivery confidence floor. Built-in strategies must clear
    ``settings.SIGNAL_MIN_CONFIDENCE``; custom (user-created) strategies BYPASS it —
    the user deliberately built the rule, so every qualifying signal from it should
    surface regardless of the generic conviction score. Use as a positional arg to
    ``.filter()`` alongside the other kwargs."""
    return Q(confidence_pct__gte=settings.SIGNAL_MIN_CONFIDENCE) | Q(service__owner__isnull=False)


def _configured_min(kind: str | None) -> int:
    """The configured floor for a kind, before it's capped to what's achievable."""
    from .pregate import KIND_REVERSION

    if kind == KIND_REVERSION:
        return max(1, int(getattr(settings, "SIGNAL_CONFLUENCE_MIN_REVERSION", 2)))
    return max(1, int(getattr(settings, "SIGNAL_CONFLUENCE_MIN", 1)))


def active_kind_counts() -> dict:
    """How many ACTIVE built-in strategies exist of each kind."""
    from .models import SignalService
    from .pregate import kind_of

    counts: dict = defaultdict(int)
    for slug in SignalService.objects.filter(
        is_active=True, owner__isnull=True
    ).values_list("slug", flat=True):
        counts[kind_of(slug)] += 1
    return counts


def confluence_min(kind: str | None = None, counts: dict | None = None) -> int:
    """Minimum distinct agreeing strategies to surface a signal (>= 1).

    Two things shape this:

    * Kinds are scored separately. Trend and reversion signals can never appear on the
      same bar (their ADX bounds are mutually exclusive), so a fade is only ever
      confirmed by other fades — of which there are far fewer.
    * The floor is CAPPED at how many active strategies of that kind exist. Otherwise
      a floor of 2 with one active fade is an impossible bar: the strategy generates
      signals that are silently binned, forever, with nothing in the logs to say so.
      Self-limiting means enabling a strategy can never quietly disable its own output.
    """
    configured = _configured_min(kind)
    if counts is None:
        counts = active_kind_counts()
    available = counts.get(kind, 0) if kind else 0
    if available:
        return max(1, min(configured, available))
    return configured


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

    Custom (user-created) strategies are EXEMPT from the K-of-N threshold: the user
    deliberately built and follows them, so each surfaces its own signal regardless
    of how many other strategies agree. Only built-in strategies are collapsed.
    """
    system = [s for s in signals if not s.service.owner_id]
    custom = [s for s in signals if s.service.owner_id]

    reps: list[Signal] = []
    kind_counts = active_kind_counts()  # once per collapse, not once per group
    for by_dir in _group(system).values():
        direction = _winning_direction(by_dir)
        if direction is None:
            continue
        svc_map = by_dir[direction]
        # The floor depends on what kind of setup this is. A group is all one kind in
        # practice (the regime bounds don't overlap), so the representative's kind
        # decides — and mixed groups take the stricter floor of the two.
        kinds = {kind_of(s.service.slug) for s in svc_map.values()}
        k = (max(confluence_min(kind, kind_counts) for kind in kinds)
             if kinds else confluence_min())
        if len(svc_map) < k:
            continue
        rep = max(svc_map.values(), key=lambda s: s.confidence_pct)
        reps.append(_annotate(rep, svc_map))

    # Each custom strategy surfaces its best signal per (symbol, timeframe, direction)
    # on its own — no threshold, agreement count is just itself.
    custom_best: dict[tuple, Signal] = {}
    for s in custom:
        key = (s.symbol_id, s.timeframe, s.direction, s.service_id)
        cur = custom_best.get(key)
        if cur is None or s.confidence_pct > cur.confidence_pct:
            custom_best[key] = s
    for s in custom_best.values():
        reps.append(_annotate(s, {s.service_id: s}))

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
