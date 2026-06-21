"""Daily signal quota by plan (Section 13.3). -1 means unlimited."""

from apps.accounts.plans import plan_for


def signal_quota_for(user) -> int:
    return plan_for(user)["signal_daily_quota"]


def strategies_allowed_for(user) -> int:
    """How many strategies this user's plan lets them follow at once."""
    return plan_for(user)["strategies"]
