"""Weekly signal quota by plan (Section 13.3). -1 means unlimited."""

from datetime import timedelta

from apps.accounts.plans import plan_for

# Custom (user-created) strategies are capped by CREATIONS over a rolling window,
# not by how many are active — deleting one never refunds a slot.
CUSTOM_STRATEGY_WINDOW = timedelta(days=30)

# Signal delivery quota is enforced over a rolling 7-day window (feed + Telegram).
SIGNAL_QUOTA_WINDOW = timedelta(days=7)


def signal_quota_for(user) -> int:
    return plan_for(user)["signal_weekly_quota"]


def strategies_allowed_for(user) -> int:
    """How many strategies this user's plan lets them follow at once."""
    return plan_for(user)["strategies"]


def custom_strategy_quota_for(user) -> dict:
    """Rolling-30-day custom-strategy creation quota for this user.

    Returns ``{limit, used, remaining, resets_at}``. ``used`` counts creations in the
    last 30 days from the append-only StrategyCreationLog, so deletions don't refund.
    ``resets_at`` is when the oldest counted creation ages out (a slot frees up), or
    None when nothing is used.
    """
    from django.utils import timezone

    from .models import StrategyCreationLog

    limit = int(plan_for(user).get("custom_strategies_per_month", 0))
    if getattr(user, "is_authenticated", False) is False:
        return {"limit": limit, "used": 0, "remaining": 0, "resets_at": None}

    since = timezone.now() - CUSTOM_STRATEGY_WINDOW
    recent = list(
        StrategyCreationLog.objects.filter(user=user, created_at__gte=since)
        .order_by("created_at")
        .values_list("created_at", flat=True)
    )
    used = len(recent)
    remaining = max(0, limit - used)
    resets_at = (recent[0] + CUSTOM_STRATEGY_WINDOW) if recent else None
    return {"limit": limit, "used": used, "remaining": remaining, "resets_at": resets_at}


def can_create_custom_strategy(user) -> bool:
    q = custom_strategy_quota_for(user)
    return q["limit"] > 0 and q["remaining"] > 0


def trim_followed_strategies(user) -> int:
    """Unfollow strategies beyond the user's effective plan cap (Section 13.3).

    The follow cap is enforced at follow-time only, so a user who followed several
    strategies on a higher plan keeps them after a downgrade. This brings them back
    to their new cap, keeping the highest-value follows: built-in strategies in
    onboarding-priority order first, then custom (Pro-only) ones — so a user leaving
    Pro loses their custom-strategy follows before their built-ins. No-op for users
    already within their cap (including unlimited plans). Returns count unfollowed.
    """
    from apps.accounts.onboarding import STRATEGY_PRIORITY

    from .models import UserSignalSubscription

    allowed = strategies_allowed_for(user)
    if allowed == -1:  # unlimited
        return 0

    subs = list(
        UserSignalSubscription.objects.filter(user=user).select_related("service")
    )
    if len(subs) <= allowed:
        return 0

    rank = {slug: i for i, slug in enumerate(STRATEGY_PRIORITY)}

    def sort_key(sub):
        svc = sub.service
        # (built-ins before custom, then by curated priority, then stable by id)
        return (svc.owner_id is not None, rank.get(svc.slug, len(rank)), sub.id)

    subs.sort(key=sort_key)
    keep_ids = {s.id for s in subs[:allowed]}
    removed, _ = (
        UserSignalSubscription.objects.filter(user=user)
        .exclude(id__in=keep_ids)
        .delete()
    )
    return removed
