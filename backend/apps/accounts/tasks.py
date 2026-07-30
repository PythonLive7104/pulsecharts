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
    from apps.signals.quota import trim_followed_strategies
    from apps.watchlists.models import WatchlistItem, watchlist_limit_for

    removed = {"watchlist": 0, "layouts": 0, "strategies": 0}

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

    # Followed strategies over the plan's follow cap (Section 13.3).
    removed["strategies"] = trim_followed_strategies(user)

    if removed["watchlist"] or removed["layouts"] or removed["strategies"]:
        logger.info(
            "plan trim: %s removed watchlist=%d layouts=%d strategies=%d",
            user.email, removed["watchlist"], removed["layouts"], removed["strategies"],
        )
    return removed


# --- expiry notification (Telegram) ---------------------------------------

# Only look back this far: if a plan expired long ago (e.g. Telegram was linked
# only after the lapse, or this task was newly deployed), don't spam an ancient
# lapse — just quietly mark it notified. Recent lapses are the ones worth telling.
EXPIRY_NOTICE_LOOKBACK_DAYS = 3


def _format_expiry_notice() -> str:
    """HTML 'your plan expired' message pointing at the subscribe page."""
    from django.conf import settings

    subscribe_url = f"{settings.FRONTEND_URL}/account/billing"
    return "\n".join([
        "⌛ <b>Your plan has expired</b>",
        "",
        "Your plan has ended, so you'll no longer receive trading signals — "
        "not here on Telegram, and not in the app.",
        "",
        # Names the two plans explicitly: "resubscribe" alone doesn't tell someone
        # coming off Starter that Pro is the other option.
        "Upgrade to <b>Starter</b> or <b>Pro</b> to start receiving them again:"
        f"\n👉 {subscribe_url}",
        "",
        "<i>Informational only. Not financial advice.</i>",
    ])


def run_expiry_notices() -> dict:
    """Tell Telegram-linked users their paid plan just lapsed and they've stopped
    receiving signals — with a resubscribe link.

    Fires once per lapse: keyed on the expired plan_expiry value via
    ``plan_expiry_notified_for`` so a user is notified at most once per
    subscription period, and re-notified after a future resubscribe+lapse.
    No-op if Telegram isn't configured.
    """
    from datetime import timedelta

    from django.db.models import F
    from django.utils import timezone

    from apps.accounts import telegram
    from .models import PlanTier, User

    if not telegram.is_configured():
        return {"notified": 0, "skipped": "telegram not configured"}

    now = timezone.now()
    cutoff = now - timedelta(days=EXPIRY_NOTICE_LOOKBACK_DAYS)
    paid_tiers = [PlanTier.STARTER, PlanTier.PRO, PlanTier.PREMIUM]

    # Lapsed paid users, linked to Telegram, not yet notified for THIS expiry.
    lapsed = (
        User.objects.filter(
            plan_tier__in=paid_tiers,
            plan_expiry__isnull=False,
            plan_expiry__lte=now,
            telegram_active=True,
        )
        .exclude(telegram_chat_id="")
        .exclude(plan_expiry_notified_for=F("plan_expiry"))
    )

    notified = 0
    for user in lapsed.iterator():
        # An old lapse (e.g. linked Telegram only after expiring, or first deploy
        # of this task): don't send a stale notice — just mark it handled so we
        # never consider it again.
        if user.plan_expiry < cutoff:
            User.objects.filter(pk=user.pk).update(plan_expiry_notified_for=user.plan_expiry)
            continue
        if telegram.send_message(user.telegram_chat_id, _format_expiry_notice()):
            User.objects.filter(pk=user.pk).update(plan_expiry_notified_for=user.plan_expiry)
            notified += 1
        # send failure (network): leave unmarked so the next tick retries.

    if notified:
        logger.info("expiry notices: notified=%d", notified)
    return {"notified": notified}


@shared_task(name="apps.accounts.tasks.notify_expired_plans")
def notify_expired_plans() -> dict:
    return run_expiry_notices()


# --- recurring upgrade nudge (Telegram) -----------------------------------


def _format_upgrade_nudge(trade_count: int) -> str:
    """HTML 'you're not getting signals' nudge pointing at the plans page."""
    from django.conf import settings

    subscribe_url = f"{settings.FRONTEND_URL}/account/billing"
    lines = ["🔒 <b>You're not receiving signals</b>", ""]
    if trade_count:
        lines += [
            f"<b>{trade_count}</b> new setup{'s' if trade_count != 1 else ''} fired in the "
            "last 24 hours. You're not getting them because trading signals are part of "
            "the <b>Starter</b> and <b>Pro</b> plans.",
        ]
    else:
        lines += [
            "Trading signals — entry, stop-loss, TP1–TP3 and the reasoning behind each "
            "call — are part of the <b>Starter</b> and <b>Pro</b> plans.",
        ]
    lines += [
        "",
        "Upgrade to Starter or Pro to get them in the app and here on Telegram:"
        f"\n👉 {subscribe_url}",
        "",
        "<i>Informational only. Not financial advice.</i>",
    ]
    return "\n".join(lines)


def _recent_trade_count(hours: int = 24) -> int:
    """Distinct trades (symbol + timeframe + direction) generated in the window.

    Counts TRADES, not signal rows: the scan writes one row per strategy, so a
    setup several strategies agree on would otherwise inflate the number we quote
    at the user. Deliberately not personalised — the nudge goes to users with no
    signal access, so there's no per-user feed to measure.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.signals.models import Signal

    since = timezone.now() - timedelta(hours=hours)
    return (
        Signal.objects.filter(
            generated_at__gte=since,
            direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
        )
        .values("symbol_id", "timeframe", "direction")
        .distinct()
        .count()
    )


def run_upgrade_nudges() -> dict:
    """Remind Telegram-linked users with NO signal access to upgrade.

    Audience is derived from the plan matrix, not from a tier list: anyone whose
    effective plan grants ``signal_weekly_quota == 0`` (today: Free, which includes
    every lapsed Starter/Pro since plan_key is expiry-aware). If a future tier gets
    signal access back, this follows automatically.

    Repeats every SIGNAL_UPGRADE_NUDGE_DAYS for as long as they have no access —
    unlike run_expiry_notices(), which fires once per lapse. A user who never paid
    has no expiry to key on, so this is the only reminder they'd ever get.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.db.models import Q
    from django.utils import timezone

    from apps.accounts import telegram
    from apps.signals.quota import signal_quota_for
    from .models import User

    if not telegram.is_configured():
        return {"nudged": 0, "skipped": "telegram not configured"}

    every_days = int(getattr(settings, "SIGNAL_UPGRADE_NUDGE_DAYS", 7) or 0)
    if every_days <= 0:
        return {"nudged": 0, "skipped": "nudges disabled"}

    now = timezone.now()
    due_before = now - timedelta(days=every_days)
    # A plan that lapsed in the last few days already triggered run_expiry_notices()
    # ("your plan has expired"). Don't stack a second message on top of it — wait for
    # the normal interval, by which point that notice is old news.
    just_lapsed_after = now - timedelta(days=EXPIRY_NOTICE_LOOKBACK_DAYS)
    candidates = (
        User.objects.filter(telegram_active=True)
        .exclude(telegram_chat_id="")
        .filter(Q(upgrade_nudge_sent_at__isnull=True) | Q(upgrade_nudge_sent_at__lte=due_before))
        .exclude(plan_expiry__gte=just_lapsed_after, plan_expiry__lte=now)
    )

    trade_count = None
    nudged = 0
    for user in candidates.iterator():
        # Expiry-aware, via the plan matrix — an active paid user is never nudged.
        if signal_quota_for(user) != 0:
            continue
        if trade_count is None:  # compute once, only if someone actually qualifies
            trade_count = _recent_trade_count()
        if telegram.send_message(user.telegram_chat_id, _format_upgrade_nudge(trade_count)):
            User.objects.filter(pk=user.pk).update(upgrade_nudge_sent_at=now)
            nudged += 1
        # send failure (network): leave the timestamp alone so the next tick retries.

    if nudged:
        logger.info("upgrade nudges: nudged=%d", nudged)
    return {"nudged": nudged}


@shared_task(name="apps.accounts.tasks.send_upgrade_nudges")
def send_upgrade_nudges() -> dict:
    return run_upgrade_nudges()


@shared_task(name="apps.accounts.tasks.enforce_plan_limits")
def enforce_plan_limits() -> dict:
    """Daily sweep: trim any user holding more saved items than their effective
    plan allows (typically a lapsed paid plan that fell back to Free).

    Only users holding more than the Free cap of any tracked type (watchlist,
    layouts, followed strategies) are even considered — anyone within the Free caps
    can never be over their limit — so the scan stays small. ``trim_to_plan_limits``
    is a no-op for users still inside their limits.
    """
    from django.db.models import Count, Q

    from apps.accounts.plans import FREE, PLANS
    from .models import User

    free_wl = PLANS[FREE]["watchlist_limit"]
    free_layouts = PLANS[FREE]["layout_limit"]
    free_strategies = PLANS[FREE]["strategies"]

    candidates = (
        User.objects.annotate(
            n_wl=Count("watchlist_items", distinct=True),
            n_layouts=Count("chart_layouts", distinct=True),
            n_subs=Count("signal_subscriptions", distinct=True),
        )
        .filter(
            Q(n_wl__gt=free_wl)
            | Q(n_layouts__gt=free_layouts)
            | Q(n_subs__gt=free_strategies)
        )
    )

    users_trimmed = wl_removed = layouts_removed = strategies_removed = 0
    for user in candidates.iterator():
        r = trim_to_plan_limits(user)
        if r["watchlist"] or r["layouts"] or r["strategies"]:
            users_trimmed += 1
            wl_removed += r["watchlist"]
            layouts_removed += r["layouts"]
            strategies_removed += r["strategies"]

    summary = {
        "users_trimmed": users_trimmed,
        "watchlist_removed": wl_removed,
        "layouts_removed": layouts_removed,
        "strategies_removed": strategies_removed,
    }
    logger.info("enforce_plan_limits: %s", summary)
    return summary
