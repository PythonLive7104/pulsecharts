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


def min_confidence(kind: str | None = None, strategy_slug: str | None = None) -> int:
    """The delivery confidence floor, resolved strategy -> kind -> global.

    Three levels because the score means different things at each. Mean reversion needs
    its own floor (``SIGNAL_MIN_CONFIDENCE_REVERSION``) since ``confidence_score`` runs a
    different branch for fades than for trend setups, so the two distributions aren't
    comparable. And within reversion the three strategies respond in three DIFFERENT
    directions — RSI(2) improves steeply as the floor rises, Bollinger Fade is flat,
    VWAP Stretch gets worse — so a per-strategy override
    (``SIGNAL_MIN_CONFIDENCE_BY_STRATEGY``) is the only way to serve all three. Numbers
    are in the settings comment.

    Both extra levels are inert until configured: 0/unset falls back to the level above.
    """
    from .pregate import KIND_REVERSION, kind_of

    base = int(settings.SIGNAL_MIN_CONFIDENCE)
    if strategy_slug:
        override = getattr(settings, "SIGNAL_MIN_CONFIDENCE_BY_STRATEGY", {}).get(strategy_slug)
        if override:
            return int(override)
        if kind is None:
            kind = kind_of(strategy_slug)
    if kind == KIND_REVERSION:
        return int(getattr(settings, "SIGNAL_MIN_CONFIDENCE_REVERSION", 0) or base)
    return base


def deliverable_q() -> Q:
    """Filter for the delivery confidence floor. Built-in strategies must clear the
    floor resolved for them (see ``min_confidence``); custom (user-created) strategies
    BYPASS it — the user deliberately built the rule, so every qualifying signal from
    it should surface regardless of the generic conviction score. Use as a positional
    arg to ``.filter()`` alongside the other kwargs."""
    from .pregate import KIND_REVERSION, STRATEGY_KIND

    base = min_confidence()
    rev = min_confidence(KIND_REVERSION)
    overrides = dict(getattr(settings, "SIGNAL_MIN_CONFIDENCE_BY_STRATEGY", {}) or {})
    custom = Q(service__owner__isnull=False)

    if rev == base and not overrides:
        return Q(confidence_pct__gte=base) | custom

    # Slug lists rather than a kind column: kind is derived in Python (STRATEGY_KIND),
    # so the DB has no way to express it. Anything unmapped is trend by definition
    # (kind_of), which is exactly what the final negated branch covers.
    rev_slugs = [slug for slug, k in STRATEGY_KIND.items() if k == KIND_REVERSION]

    q = custom
    for slug, floor in overrides.items():
        q |= Q(service__slug=slug, confidence_pct__gte=int(floor))
    # Reversion strategies WITHOUT an override fall to the kind floor.
    rest_rev = [s for s in rev_slugs if s not in overrides]
    if rest_rev:
        q |= Q(service__slug__in=rest_rev, confidence_pct__gte=rev)
    # Everything else — trend, breakout, and any slug not in STRATEGY_KIND.
    q |= Q(confidence_pct__gte=base) & ~Q(
        service__slug__in=list(overrides) + rev_slugs
    )
    return q


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
    """(symbol_id, timeframe, kind) -> {direction: {service_id: best signal per service}}.

    Keeps only the highest-confidence signal per service per direction, so a
    strategy that somehow fired twice still counts as one vote.

    KIND is part of the key because trend and reversion are separate populations that
    must not compete for one slot. Grouped together, two trend signals (short of their
    floor of 3) outvoted a lone fade and then failed the floor themselves — so the
    fade was crowded out by a cluster that didn't qualify either, and nothing surfaced
    at all. They can't co-fire on the same bar anyway (opposite ADX bounds), so they
    only ever meet here because the feed looks back over 2 days, across regimes.
    """
    groups: dict = defaultdict(lambda: defaultdict(dict))
    for s in signals:
        svc_map = groups[(s.symbol_id, s.timeframe, kind_of(s.service.slug))][s.direction]
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
    that meets the confluence threshold for ITS KIND. Each representative is annotated with
    ``.confluence_count`` / ``.confluence_services``. Returned newest-first.

    Custom (user-created) strategies are EXEMPT from the K-of-N threshold: the user
    deliberately built and follows them, so each surfaces its own signal regardless
    of how many other strategies agree. Only built-in strategies are collapsed.
    """
    system = [s for s in signals if not s.service.owner_id]
    custom = [s for s in signals if s.service.owner_id]

    reps: list[Signal] = []
    kind_counts = active_kind_counts()  # once per collapse, not once per group
    # Each kind is scored against its OWN floor, then at most one card survives per
    # (symbol, timeframe) — so a user is never shown a BUY and a SELL for the same
    # chart just because the regime changed within the lookback window.
    best_per_chart: dict[tuple, Signal] = {}
    for (symbol_id, timeframe, kind), by_dir in _group(system).items():
        direction = _winning_direction(by_dir)
        if direction is None:
            continue
        svc_map = by_dir[direction]
        if len(svc_map) < confluence_min(kind, kind_counts):
            continue
        rep = _annotate(max(svc_map.values(), key=lambda s: s.confidence_pct), svc_map)
        key = (symbol_id, timeframe)
        cur = best_per_chart.get(key)
        if cur is None or (rep.confluence_count, rep.confidence_pct) > (
            cur.confluence_count, cur.confidence_pct
        ):
            best_per_chart[key] = rep
    reps.extend(best_per_chart.values())

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


# --- correlated-exposure cap ------------------------------------------------
# An FX pair is two currencies, so SELL EUR-USD is short EUR AND long USD. Fade
# strategies all trigger on the same condition — price extended from its mean — so a
# single strong USD move makes every USD pair extended at the same instant and
# Bollinger Fade fires SELL on all of them at once. Delivered as-is that is not four
# signals, it is one bet sent four times: they win together and, as happened on
# 2026-08-14 (GBP-USD, EUR-USD, NZD-USD, AUD-USD all stopped out inside 20 minutes),
# they lose together.
#
# This never showed before forex went fade-only (2026-08-12). With six trend
# strategies also live, a hard USD trend produced trend signals WITH the move and
# fades AGAINST it, and that opposition diversified the book invisibly. Removing the
# trend strategies raised each strategy's win rate and concentrated the portfolio —
# the cap restores the diversification without giving the win rate back.
#
# Crypto is deliberately exempt: alts do co-move with BTC, but a ticker like
# "BTC-USD" has no second traded currency to net against, so the same rule would just
# collapse every crypto signal into one USD bucket.


def _fx_exposure(signal) -> tuple:
    """(currency, side) pairs a forex signal takes on. Empty for non-forex.

    BUY BASE-QUOTE  = long BASE,  short QUOTE
    SELL BASE-QUOTE = short BASE, long QUOTE
    """
    sym = signal.symbol
    if getattr(sym, "asset_class", "crypto") != "forex":
        return ()
    parts = (sym.ticker or "").split("-")
    if len(parts) != 2:
        return ()
    base, quote = parts
    long_base = signal.direction == Signal.Direction.BUY
    return ((base, "long" if long_base else "short"),
            (quote, "short" if long_base else "long"))


def cap_currency_exposure(reps: list, *, already_open=()) -> list:
    """Drop signals that would stack a currency bet beyond SIGNAL_MAX_PER_CURRENCY.

    `already_open` is the user's live positions, so the cap counts across scans rather
    than only within one batch — four correlated calls arriving in four consecutive
    scans is the same bet as four arriving together.

    Order is preserved and the FIRST signal to claim an exposure wins, so callers keep
    control of priority (the feed sorts newest-first, Telegram oldest-first). 0
    disables the cap entirely.
    """
    cap = int(getattr(settings, "SIGNAL_MAX_PER_CURRENCY", 0) or 0)
    if cap <= 0:
        return reps

    counts: dict = {}
    for sig in already_open:
        for key in _fx_exposure(sig):
            counts[key] = counts.get(key, 0) + 1

    kept = []
    for sig in reps:
        exposure = _fx_exposure(sig)
        if exposure and any(counts.get(k, 0) >= cap for k in exposure):
            continue  # this bet is already on
        for key in exposure:
            counts[key] = counts.get(key, 0) + 1
        kept.append(sig)
    return kept


def shadowed_asset_classes() -> set:
    """Asset classes generated + evaluated but never delivered.

    Per-asset-class SIGNAL_SHADOW_MODE. `feed_stats` reads stored Signal rows rather
    than deliveries, so a shadowed class still accumulates a real, measurable track
    record — which is the point: validate forex on live data without putting untested
    signals in front of anyone, while crypto ships normally.
    """
    return {a.strip() for a in (getattr(settings, "SIGNAL_SHADOW_ASSET_CLASSES", None) or ()) if a.strip()}
