"""The single place a paid plan is granted.

Extracted from the Paystack webhook so MANUAL CRYPTO APPROVALS take the identical
path: same Subscription row, same tier/expiry arithmetic, same referral commission,
same provisioning top-up, same confirmation email and operator alert. Two payment
rails writing their own version of this would drift, and the rules encoded here are
not obvious — paid time EXTENDS rather than replaces, a perpetual account is never
demoted by a later smaller purchase, and an active higher tier survives a lower-tier
payment. Those were all bugs once.

Idempotent on `payment_ref`: re-running with the same reference updates the existing
Subscription instead of granting twice. That is what makes the admin approve action
safe to double-click, and what made the Paystack webhook safe to retry.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import PlanTier, Subscription
from apps.accounts.plans import (
    LIFETIME,
    LIFETIME_PLAN,
    PRO,
    has_perpetual_access,
    plan_key,
    plan_rank,
    tier_granted_by,
)
from apps.common.email import send_payment_admin_alert, send_payment_confirmation_email

logger = logging.getLogger("billing.grant")

# Days of access granted per successful one-time payment.
#
# 30 since 2026-09-23 (was 31). Applies to NEW grants only — Subscription rows
# already written kept the 31-day renewal_date they were granted, and nothing
# recalculates them. Paid time also EXTENDS rather than replaces, so a user buying
# again mid-plan adds 30 days to whatever is left.
GRANT_DAYS = 30


def apply_paid_grant(user, plan: str, reference: str, paid: int) -> None:
    """Grant `plan` to `user` off a payment already CONFIRMED by the caller.

    This function does not verify anything — the caller is responsible for knowing
    the money arrived (Paystack verifies server-side; crypto is checked on-chain by a
    human). `paid` is in cents and is used only for the commission and the operator
    alert.
    """
    lifetime = plan == LIFETIME
    granted = tier_granted_by(plan)  # lifetime -> pro
    tier = PlanTier.PRO if granted == PRO else PlanTier.STARTER
    # A null renewal/expiry is what marks access as permanent.
    # Paid time EXTENDS whatever paid time is left instead of replacing it —
    # buying the next month on day 20 must not vaporise those 20 days. (Trial
    # promo codes deliberately don't stack; money does.)
    now = timezone.now()
    base = user.plan_expiry if (user.plan_expiry and user.plan_expiry > now) else now
    renewal = None if lifetime else base + timedelta(days=GRANT_DAYS)

    # Never demote someone whose access already never expires — a later
    # Starter/Pro payment must not hand a permanent account a 31-day expiry.
    if has_perpetual_access(user) and not lifetime:
        renewal = None
        if plan_rank(user.plan_tier) > plan_rank(tier):
            tier = user.plan_tier
    # Same rule for a still-active TIMED plan: an active Pro user who buys Starter
    # keeps Pro over the extended window instead of being dropped a tier by their
    # own payment. plan_key() is expiry-aware, so a lapsed Pro doesn't count.
    elif plan_rank(plan_key(user)) > plan_rank(tier):
        tier = user.plan_tier

    Subscription.objects.update_or_create(
        user=user,
        payment_ref=reference,
        defaults={
            "tier": tier,
            "status": Subscription.Status.ACTIVE,
            "renewal_date": renewal,
        },
    )
    user.plan_tier = tier
    user.plan_expiry = renewal
    user.save(update_fields=["plan_tier", "plan_expiry"])
    logger.info(
        "Paystack charge.success: %s -> %s (expiry=%s)",
        user.email, tier, renewal or "never (lifetime)",
    )

    # Credit whoever referred this user, if anyone. Never let a commission
    # failure break the grant — the customer's access matters more, and the row
    # can be reconstructed from the payment later.
    try:
        _record_commission(user, reference, plan, paid)
    except Exception:
        logger.exception("Referral commission failed for %s (%s)", user.email, reference)

    # Top the user's watchlist + followed strategies up to the new plan's
    # defaults (idempotent). Never let it break webhook processing.
    try:
        from apps.accounts.onboarding import provision_default_setup

        provision_default_setup(user)
    except Exception:
        logger.exception("Upgrade provisioning failed for %s", user.email)

    # `tier` may be a plain str when carried over from user.plan_tier above.
    label = LIFETIME_PLAN["label"] if lifetime else PlanTier(tier).label
    send_payment_confirmation_email(to=user.email, plan_label=label, renewal=renewal)

    # Operator alert. Read the commission back rather than threading it out of
    # _record_commission, so the alert reports what was actually persisted (and
    # stays correct on a replayed webhook, where nothing new is written). Wrapped
    # like every other post-grant step: the customer already has their access and a
    # failed notification must never fail the webhook — Paystack would retry it.
    try:
        from apps.accounts.models import ReferralCommission

        row = (
            ReferralCommission.objects.filter(payment_ref=reference)
            .select_related("referrer")
            .first()
        )
        send_payment_admin_alert(
            user_email=user.email,
            plan_label=label,
            amount_usd=paid / 100,
            reference=reference,
            renewal=renewal,
            referrer_email=row.referrer.email if row and row.referrer else "",
            commission_usd=row.commission_usd if row else None,
        )
    except Exception:
        logger.exception("Payment admin alert failed for %s (%s)", user.email, reference)


def _record_commission(user, reference: str, plan: str, paid_cents: int) -> None:
    """Record the referrer's cut of a verified payment.

    No-op unless the payer signed up with someone's code and the rate is set.
    `get_or_create` on the payment reference makes a replayed webhook harmless —
    the same charge can never pay a referrer twice.
    """
    from decimal import Decimal

    from apps.accounts.models import ReferralCode, ReferralCommission

    rate = Decimal(str(getattr(settings, "REFERRAL_COMMISSION_PCT", 0) or 0))
    code_str = (user.referred_by_code or "").strip()
    if rate <= 0 or not code_str:
        return
    # SUBSCRIPTION plans only — lifetime pays NO commission. A lifetime purchase is
    # one-time revenue against a perpetual cost to serve, so a 20% cut of it is paid
    # out of margin that never recurs; Starter/Pro commissions come out of revenue
    # that does. The referrer still earns their signup credit on a lifetime buyer.
    if plan == LIFETIME:
        logger.info(
            "No referral commission on lifetime purchase %s (%s)", reference, user.email
        )
        return
    code = ReferralCode.objects.filter(code=code_str.upper()).select_related("owner").first()
    # No owner = an admin promo code, not somebody's referral link. And nobody
    # earns a commission on their own payment.
    if code is None or code.owner_id is None or code.owner_id == user.id:
        return

    amount = (Decimal(paid_cents) / Decimal(100)).quantize(Decimal("0.01"))
    commission = (amount * rate / Decimal(100)).quantize(Decimal("0.01"))
    if commission <= 0:
        return
    _, created = ReferralCommission.objects.get_or_create(
        payment_ref=reference,
        defaults={
            "referrer": code.owner,
            "referred_user": user,
            "referred_email": user.email,
            "code": code.code,
            "plan": plan,
            "amount_usd": amount,
            "rate_pct": rate,
            "commission_usd": commission,
        },
    )
    if created:
        logger.info(
            "Referral commission: %s earns $%s (%s%% of $%s) from %s [%s]",
            code.owner.email, commission, rate, amount, user.email, reference,
        )
