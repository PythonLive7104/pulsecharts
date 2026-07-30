"""Weekly signal quota by plan (Section 13.3). -1 means unlimited."""

import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.plans import FREE, plan_for

# Custom (user-created) strategies are capped by CREATIONS over a rolling window,
# not by how many are active — deleting one never refunds a slot.
CUSTOM_STRATEGY_WINDOW = timedelta(days=30)

# Signal delivery quota is enforced over a rolling 7-day window (feed + Telegram).
SIGNAL_QUOTA_WINDOW = timedelta(days=7)


def free_trial_days_left(user) -> int | None:
    """Whole days left in a new free account's signal trial, or None if it doesn't
    apply (paid plan, trial over, or an account that has ever had paid access).

    The trial is measured from ``date_joined`` and runs once per ACCOUNT, not per
    plan: the gate is ``plan_expiry is None``, which is only ever true for someone
    who has never been granted a paid tier — no payment, no admin code, no
    plan-granting referral code. So a lapsed Starter/Pro can't slide back into a
    second free run of signals, and neither can someone whose signup referral code
    already gave them 30 days of Starter (their grant set plan_expiry).
    """
    days = int(getattr(settings, "SIGNAL_FREE_TRIAL_DAYS", 0) or 0)
    if days <= 0:
        return None
    plan = plan_for(user)
    if plan["key"] != FREE or not plan.get("signal_trial_weekly_quota"):
        return None
    if getattr(user, "plan_expiry", None) is not None:
        return None  # has had paid access at some point — trial spent
    joined = getattr(user, "date_joined", None)
    if joined is None:
        return None
    left = (joined + timedelta(days=days)) - timezone.now()
    if left.total_seconds() <= 0:
        return None  # trial over — Free's real quota (0) applies
    # Round UP: with 2 hours left a user should read "1 day", never "0 days left".
    return math.ceil(left.total_seconds() / 86400)


def free_trial_expired(user) -> bool:
    """True for a free account whose signup signal trial has run out.

    Lets the locked card say "your free trial has ended" to someone who has been
    getting signals all month, instead of "signals are a paid feature" — which
    would read as if they'd never had them. False when the trial is switched off
    entirely, and false for anyone who has had paid access (they get the plain
    upgrade message, since what ended for them was a plan, not a trial).
    """
    days = int(getattr(settings, "SIGNAL_FREE_TRIAL_DAYS", 0) or 0)
    if days <= 0:
        return False
    plan = plan_for(user)
    if plan["key"] != FREE or not plan.get("signal_trial_weekly_quota"):
        return False
    if getattr(user, "plan_expiry", None) is not None:
        return False
    return free_trial_days_left(user) is None


def signal_quota_for(user) -> int:
    """Signals/week for this user. 0 = no access (locked upgrade card), -1 = unlimited.

    New free accounts get a time-boxed taste of the feed — see free_trial_days_left —
    after which Free drops to 0 and signals are paid-only.
    """
    plan = plan_for(user)
    if plan["key"] == FREE and free_trial_days_left(user) is not None:
        return plan["signal_trial_weekly_quota"]
    return plan["signal_weekly_quota"]


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
