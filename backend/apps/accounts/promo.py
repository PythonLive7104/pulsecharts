"""Admin access codes (settings.ADMIN_PRO_CODE / ADMIN_STARTER_CODE).

Shared by BOTH redemption paths so they can't drift:

  * at SIGNUP, typed into the same box as a referral code (RegisterSerializer) —
    people are handed "a code" and try it at the first field they see;
  * after signup, on Plan & Billing (RedeemPromoCodeView).

The rules live here, once:

  * ONE access code per account, ever — checked across BOTH code fields, so
    redeeming Starter then Pro is blocked and rotating a code value doesn't reopen
    the door for someone who already used the old one;
  * a grant NEVER shortens existing access (max of what they have and what the code
    gives) and never stacks onto it — a "30-day" code lands 30 days out, not 45;
  * never downgrade a user who is already on a higher tier, and never put an expiry
    date on access that currently has none.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone


class PromoError(ValueError):
    """A code was recognised but can't be applied to this user."""


def active_codes() -> list[tuple]:
    """[(code, tier, days, user_field, label), …] for every configured code.

    Pro first, so a value that somehow matched both is treated as the higher grant.
    """
    from .plans import PRO, STARTER

    configured = [
        (settings.ADMIN_PRO_CODE, PRO, settings.ADMIN_PRO_DAYS,
         "pro_promo_code_used", "Pro"),
        (settings.ADMIN_STARTER_CODE, STARTER, settings.ADMIN_STARTER_DAYS,
         "starter_promo_code_used", "Starter"),
    ]
    return [(c.strip(), tier, days, field, label)
            for c, tier, days, field, label in configured if (c or "").strip()]


def match(entered: str):
    """The configured code matching `entered` (case-insensitive), or None."""
    value = (entered or "").strip()
    if not value:
        return None
    return next((m for m in active_codes() if value.upper() == m[0].upper()), None)


def check_redeemable(user, matched) -> None:
    """Raise PromoError if this user may not redeem `matched`. No-op otherwise.

    Every check is vacuous for a brand-new signup (no prior code, Free tier, no
    expiry) — it's applied there anyway so the two paths share one definition of
    "allowed" rather than one of them quietly permitting more.
    """
    from .plans import has_perpetual_access, plan_key, plan_rank

    _code, tier, _days, _field, label = matched

    already = (user.pro_promo_code_used or "").strip() or (
        user.starter_promo_code_used or ""
    ).strip()
    if already:
        raise PromoError("You've already redeemed an access code.")
    if has_perpetual_access(user):
        raise PromoError("Your plan doesn't expire — no code needed.")
    if plan_rank(plan_key(user)) > plan_rank(tier):
        raise PromoError(f"You're already on a higher plan than {label}.")


def apply_grant(user, matched) -> dict:
    """Grant the plan and record which code was used. Assumes check_redeemable passed."""
    code, tier, days, field, _label = matched

    granted_until = timezone.now() + timedelta(days=days)
    user.plan_tier = tier
    # max(): never shorten a longer plan. Not `+=`: a 30-day code grants 30 days from
    # today, never 30 on top of whatever was left.
    user.plan_expiry = (
        max(user.plan_expiry, granted_until) if user.plan_expiry else granted_until
    )
    setattr(user, field, code)
    user.save(update_fields=["plan_tier", "plan_expiry", field])
    return {"tier": tier, "days": days, "expiry": user.plan_expiry}
