"""Default first-run setup for a user's watchlist and followed strategies.

Many users don't know how to set up a watchlist or follow strategies, land on an
empty dashboard, and bounce. To remove that friction we seed sensible defaults
sized by plan (apps.accounts.plans): a ready-made watchlist of the top coins and
a set of pre-followed strategies.

The single entry point is `provision_default_setup(user)`. It's idempotent — it
only adds what's missing — so it's safe to call again on upgrade or from the
`provision_defaults` backfill command.
"""

from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger("accounts")

# Curated priority order for the strategies we follow by default. Mirrors the
# seed curation (apps/signals/management/commands/seed_signal_services.py). Only
# active services are ever followed; any active service not listed here is
# appended after these (ordered by id), so a newly added strategy still gets
# picked up for Pro's "follow everything".
# Order matters: Free follows the first 4 and Starter the first 6, so anything below
# position 6 is invisible to every tier except Pro. `bb-fade` sits at 3 deliberately —
# it is the only NON-trend strategy, and the whole point of adding it is that it pays
# when the trend book doesn't. Leaving it at the end (where an unlisted slug lands)
# would mean no Free or Starter user ever received a mean-reversion signal.
# Inactive strategies are skipped, so listing one before it's activated is harmless.
STRATEGY_PRIORITY = [
    # 1-4: TREND. Free follows exactly these, so it keeps 4 trend strategies and the
    # 3-of-4 confluence floor stays reachable with slack.
    "momentum-crossover",
    "macd-trend-following",
    "trend-rider",
    "vwap-trend",
    # 5-7: MEAN REVERSION. Ranked here on purpose — Starter and Pro pick
    # them up by default, Free (4) does not. A fade inside Free's four would leave
    # only 3 trend strategies followed and force unanimity on the trend feed.
    "bb-fade",
    "vwap-stretch",
    "rsi2-reversion",
    "bollinger-breakout",
    "volatility-breakout",
    "trend-pullback",
]


def _ordered_active_services():
    """Active BUILT-IN signal services in default-follow priority order.

    ``owner__isnull=True`` is a privacy boundary, not a tidy-up. Custom strategies are
    private to the user who built them, and Pro's ``default_strategies = -1`` means
    "follow every active strategy" — so without this filter, provisioning a Pro user
    subscribed them to every OTHER user's private custom strategy, and those users'
    signals were then delivered into their feed and Telegram. Never auto-follow a
    strategy someone else owns; a user only ever follows their own (auto-subscribed at
    creation, see SignalServiceListView.post).
    """
    from apps.signals.models import SignalService

    services = list(SignalService.objects.filter(is_active=True, owner__isnull=True))
    rank = {slug: i for i, slug in enumerate(STRATEGY_PRIORITY)}
    services.sort(key=lambda s: (rank.get(s.slug, len(rank)), s.id))
    return services


@transaction.atomic
def provision_default_setup(user, as_plan: str | None = None, include_forex: bool = False) -> dict:
    """Seed `user` with a default watchlist + followed strategies for their plan.

    - Watchlist: the top N active *crypto* symbols (by curated sort order), where
      N is the plan's `default_watchlist`. N = -1 (Pro) means every active symbol,
      forex included.
    - Strategies: the top N active strategies by priority, where N is the plan's
      `default_strategies` (-1 = every active strategy).

    ``as_plan`` sizes the defaults as if the user were on that plan (e.g. "pro")
    instead of their actual one — for provisioning an admin/monitoring account with
    full coverage without having to put a fake subscription on it. It does NOT grant
    the plan: entitlements, quotas and gating still read the user's real plan, so an
    over-seeded watchlist is only ever a superset of what they'd otherwise watch.

    ``include_forex`` also seeds the active forex pairs, which signup never does
    (it's crypto-only). Useful for an account that needs to see every signal the
    engine produces, forex included.

    Idempotent: symbols/strategies the user already has are skipped, so re-running
    never duplicates. Returns a summary of what was added (for logging/commands).
    """
    from apps.accounts.plans import PLANS, plan_for
    from apps.market_data.models import Symbol
    from apps.signals.models import UserSignalSubscription
    from apps.watchlists.models import WatchlistItem

    plan = PLANS[as_plan] if as_plan else plan_for(user)

    # --- Watchlist: top N active crypto symbols ---
    want_symbols = plan.get("default_watchlist", 0)
    added_symbols = 0
    if want_symbols:
        # -1 = "every symbol we track" (Pro). Unlike the numeric tiers that seed a
        # crypto-only top-N, this includes forex — it mirrors an uncapped
        # watchlist_limit, so the seeded list IS the whole roster.
        all_symbols = want_symbols == -1
        if all_symbols:
            include_forex = True
        existing_ids = set(
            WatchlistItem.objects.filter(user=user).values_list("symbol_id", flat=True)
        )
        crypto_qs = Symbol.objects.filter(
            is_active=True, asset_class=Symbol.AssetClass.CRYPTO
        ).order_by("sort_order", "ticker")
        top_symbols = list(crypto_qs if all_symbols else crypto_qs[:want_symbols])
        if include_forex:
            top_symbols += list(
                Symbol.objects.filter(
                    is_active=True, asset_class=Symbol.AssetClass.FOREX
                ).order_by("sort_order", "ticker")
            )
        start = len(existing_ids)
        new_items = [
            WatchlistItem(user=user, symbol=sym, sort_order=start + i)
            for i, sym in enumerate(s for s in top_symbols if s.id not in existing_ids)
        ]
        if new_items:
            WatchlistItem.objects.bulk_create(new_items, ignore_conflicts=True)
        added_symbols = len(new_items)

    # --- Strategies: follow N (or all, -1) active services by priority ---
    want_strategies = plan.get("default_strategies", 0)
    added_strategies = 0
    if want_strategies:
        services = _ordered_active_services()
        if want_strategies > 0:
            services = services[:want_strategies]
        followed_ids = set(
            UserSignalSubscription.objects.filter(user=user).values_list(
                "service_id", flat=True
            )
        )
        new_subs = [
            UserSignalSubscription(user=user, service=svc)
            for svc in services
            if svc.id not in followed_ids
        ]
        if new_subs:
            UserSignalSubscription.objects.bulk_create(new_subs, ignore_conflicts=True)
        added_strategies = len(new_subs)

    return {"symbols": added_symbols, "strategies": added_strategies}
