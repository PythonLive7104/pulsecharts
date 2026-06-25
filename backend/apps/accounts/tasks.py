"""Plan-limit enforcement (Section 11, 12).

Entitlements are enforced lazily at read time (apps.accounts.plans.plan_key drops
an expired paid plan to Free automatically — no downgrade job needed for *gating*).
But saved data accumulated on a paid plan — watchlist symbols, chart layouts —
isn't gated on read, so a lapsed user would otherwise keep more saved items than
the Free tier allows. This module trims that excess back to the user's *effective*
(expiry-aware) limit.

Two entry points:
  - ``trim_to_plan_limits(user)`` — synchronous, called right when a plan is
    revoked (billing webhook) so the downgrade is immediate.
  - ``enforce_plan_limits`` — a daily Celery task that catches the silent-lapse
    case (a plan that simply expired with no webhook).
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def trim_to_plan_limits(user) -> dict:
    """Delete watchlist items and chart layouts beyond the user's effective plan
    limit, keeping the highest-priority ones: the top of the user's watchlist
    order and their most recently saved layouts.

    Idempotent and safe to call on anyone — it's a no-op for users already within
    their limits (including anyone still on an active paid plan, since
    ``*_limit_for`` is expiry-aware). Returns the counts removed.
    """
    from apps.chart_layouts.models import ChartLayout, layout_limit_for
    from apps.watchlists.models import WatchlistItem, watchlist_limit_for

    removed = {"watchlist": 0, "layouts": 0}

    wl_limit = watchlist_limit_for(user)
    if wl_limit != -1:  # -1 == unlimited
        keep = list(
            WatchlistItem.objects.filter(user=user)
            .order_by("sort_order", "created_at")
            .values_list("id", flat=True)[:wl_limit]
        )
        removed["watchlist"] = (
            WatchlistItem.objects.filter(user=user).exclude(id__in=keep).delete()[0]
        )

    layout_limit = layout_limit_for(user)
    if layout_limit != -1:
        keep = list(
            ChartLayout.objects.filter(user=user)
            .order_by("-saved_at")
            .values_list("id", flat=True)[:layout_limit]
        )
        removed["layouts"] = (
            ChartLayout.objects.filter(user=user).exclude(id__in=keep).delete()[0]
        )

    if removed["watchlist"] or removed["layouts"]:
        logger.info(
            "plan trim: %s removed watchlist=%d layouts=%d",
            user.email, removed["watchlist"], removed["layouts"],
        )
    return removed


@shared_task(name="apps.accounts.tasks.enforce_plan_limits")
def enforce_plan_limits() -> dict:
    """Daily sweep: trim any user holding more saved items than their effective
    plan allows (typically a lapsed paid plan that fell back to Free).

    Only users holding more than the Free cap of either type are even considered —
    anyone within the Free caps can never be over their limit — so the scan stays
    small. ``trim_to_plan_limits`` is a no-op for users still inside their limits.
    """
    from django.db.models import Count, Q

    from apps.accounts.plans import FREE, PLANS
    from .models import User

    free_wl = PLANS[FREE]["watchlist_limit"]
    free_layouts = PLANS[FREE]["layout_limit"]

    candidates = (
        User.objects.annotate(
            n_wl=Count("watchlist_items", distinct=True),
            n_layouts=Count("chart_layouts", distinct=True),
        )
        .filter(Q(n_wl__gt=free_wl) | Q(n_layouts__gt=free_layouts))
    )

    users_trimmed = wl_removed = layouts_removed = 0
    for user in candidates.iterator():
        r = trim_to_plan_limits(user)
        if r["watchlist"] or r["layouts"]:
            users_trimmed += 1
            wl_removed += r["watchlist"]
            layouts_removed += r["layouts"]

    summary = {
        "users_trimmed": users_trimmed,
        "watchlist_removed": wl_removed,
        "layouts_removed": layouts_removed,
    }
    logger.info("enforce_plan_limits: %s", summary)
    return summary
