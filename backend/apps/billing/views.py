"""Billing endpoints (Section 9).

POST /api/billing/checkout/  — start a Paystack payment (auth required).
POST /api/billing/webhook/   — Paystack -> us, grants the plan on charge.success.
GET  /api/billing/history/   — the user's subscription records.

Billing model: one-time payments that grant 30 days of access (apps/billing/paystack.py).
Access lapses automatically at plan_expiry (accounts.plans.plan_key + the daily
trim sweep), so no cancel/renewal webhooks are needed.

The 'lifetime' option is the exception: it grants the Pro tier with a NULL
plan_expiry, which plan_key() reads as never-expiring. It is a purchase option,
not a fourth tier — see apps.accounts.plans.LIFETIME_PLAN.

Money can also flow back out: a chargeback (charge.dispute.create) or a refund
(refund.processed) revokes the grant its payment bought. This matters most for
lifetime, which has no plan_expiry for the daily sweep to lapse — without an
explicit revoke, a charged-back lifetime purchase would grant Pro forever.
Subscription rows are marked, never deleted: a chargeback has to be contested with
evidence, and deleting the account destroys exactly that evidence.
"""

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import PlanTier, Subscription
from apps.accounts.plans import (
    LIFETIME,
    LIFETIME_PLAN,
    PRO,
    PURCHASE_OPTIONS,
    is_lifetime,
    plan_rank,
    tier_granted_by,
)
from apps.accounts.tasks import trim_to_plan_limits
from apps.common.email import send_payment_confirmation_email

from .paystack import (
    PaystackError,
    create_checkout_session,
    plan_amount_cents,
    verify_transaction,
    verify_webhook_signature,
)
from .serializers import SubscriptionSerializer

logger = logging.getLogger("billing")

# Days of access granted per successful one-time payment.
GRANT_DAYS = 31


class SubscriptionHistoryView(ListAPIView):
    """GET /api/billing/history/ — the authenticated user's subscription records,
    newest first (model Meta already orders by -created_at)."""

    serializer_class = SubscriptionSerializer

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)


class CheckoutView(APIView):
    """Start a premium payment."""

    def post(self, request):
        plan = request.data.get("plan", "pro")
        if plan not in PURCHASE_OPTIONS:
            return Response(
                {"detail": "Choose a paid plan: 'starter', 'pro' or 'lifetime'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Nothing left to sell someone whose access already never expires.
        if is_lifetime(request.user):
            return Response(
                {"detail": "You're on the lifetime plan — no further payment needed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            session = create_checkout_session(
                user=request.user,
                plan=plan,
                success_url=f"{settings.FRONTEND_URL}/billing/success",
                cancel_url=f"{settings.FRONTEND_URL}/billing/cancel",
            )
        except (PaystackError, NotImplementedError) as exc:
            # Billing not configured yet — surface a clean "coming soon".
            return Response(
                {"detail": str(exc), "billing_live": False},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(session, status=status.HTTP_201_CREATED)


class WebhookView(APIView):
    """Paystack webhook. Public endpoint, authenticated by signature."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        if not verify_webhook_signature(request.body, request.headers):
            return Response(
                {"detail": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            event = json.loads(request.body)
        except json.JSONDecodeError:
            return Response(
                {"detail": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST
            )

        self._handle_event(event)
        # Always 200 on a well-formed, signed event so Paystack doesn't retry a
        # payload we've deliberately ignored.
        return Response({"received": True})

    def _resolve_user(self, data):
        """Link an event to a user via metadata.user_id, falling back to email."""
        from apps.accounts.models import User

        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        if user_id:
            user = User.objects.filter(pk=user_id).first()
            if user:
                return user
        email = (data.get("customer") or {}).get("email")
        if email:
            user = User.objects.filter(email__iexact=email).first()
            if user:
                return user
        logger.warning("Paystack webhook: could not resolve user (user_id=%s)", user_id)
        return None

    def _handle_event(self, event: dict) -> None:
        """Route a signed Paystack event. Money in grants access; money back out
        revokes it. Everything else is logged and ignored."""
        event_type = event.get("event", "")
        data = event.get("data", {}) or {}

        if event_type == "charge.success":
            self._grant(data)
        elif event_type == "charge.dispute.create":
            self._revoke(data, Subscription.Status.DISPUTED)
        elif event_type == "refund.processed":
            self._revoke(data, Subscription.Status.REFUNDED)
        elif event_type == "charge.dispute.resolve":
            # Paystack's `resolution` values don't map cleanly onto "we won", so
            # reinstating automatically risks handing access back to a fraudster.
            # Surface it loudly instead; staff restore with `manage.py set_plan`.
            logger.warning(
                "Paystack dispute resolved (ref=%s, resolution=%s) — access stays "
                "revoked; restore manually if we won.",
                self._reference_from(data), data.get("resolution"),
            )
        else:
            logger.info("Ignoring Paystack event: %s", event_type)

    @staticmethod
    def _reference_from(data: dict) -> str:
        """The original charge's reference. Dispute and refund payloads nest or
        rename it, so check every shape Paystack uses."""
        return (
            data.get("transaction_reference")
            or (data.get("transaction") or {}).get("reference")
            or data.get("reference")
            or ""
        )

    def _revoke(self, data: dict, new_status: str) -> None:
        """Money came back out — undo the grant that payment bought.

        Idempotent: a re-sent dispute webhook is a no-op. Only touches the plan if
        the revoked payment is what's actually backing the user's current access,
        so a chargeback on an old, superseded payment can't strip a plan the user
        later paid for (or was granted by promo code).
        """
        reference = self._reference_from(data)
        sub = Subscription.objects.filter(payment_ref=reference).select_related("user").first()
        if sub is None:
            logger.warning("Paystack %s: no subscription for reference %s", new_status, reference)
            return
        if sub.status in (Subscription.Status.DISPUTED, Subscription.Status.REFUNDED):
            return  # already handled

        sub.status = new_status
        sub.save(update_fields=["status", "updated_at"])
        user = sub.user
        logger.warning(
            "Paystack %s: %s on %s (%s) — subscription marked",
            new_status, user.email, sub.tier, reference,
        )

        if not self._grant_is_current(user, sub):
            logger.info("Revoked payment %s wasn't backing current access — plan untouched", reference)
            return

        self._downgrade_to_best_remaining(user, exclude=sub)

    @staticmethod
    def _grant_is_current(user, sub) -> bool:
        """True if `sub` is the payment currently backing the user's access — i.e.
        the plan fields still hold exactly what this row granted. A lifetime row
        granted a null expiry; a timed row granted its renewal_date."""
        if user.plan_tier != sub.tier:
            return False
        if sub.renewal_date is None:  # lifetime grant
            return user.plan_expiry is None
        return user.plan_expiry == sub.renewal_date

    @staticmethod
    def _downgrade_to_best_remaining(user, *, exclude) -> None:
        """Fall back to the user's best still-valid paid subscription, or Free.

        Prevents a refund on a lifetime purchase from wiping out an unrelated,
        still-active monthly plan the same user paid for.
        """
        now = timezone.now()
        candidates = [
            s
            for s in user.subscriptions.filter(status=Subscription.Status.ACTIVE).exclude(pk=exclude.pk)
            if s.renewal_date is None or s.renewal_date > now
        ]
        if candidates:
            # Highest tier wins; among equals prefer a lifetime row (null renewal),
            # then the one that runs longest.
            best = max(
                candidates,
                key=lambda s: (plan_rank(s.tier), s.renewal_date is None, s.renewal_date or now),
            )
            user.plan_tier = best.tier
            user.plan_expiry = best.renewal_date
        else:
            user.plan_tier = PlanTier.FREE
            user.plan_expiry = None
        user.save(update_fields=["plan_tier", "plan_expiry"])

        # Mirror the downgrade path used by set_plan / the daily sweep: prune
        # watchlist + layouts now over the new plan's limits. Never let a
        # provisioning hiccup fail the webhook.
        try:
            trim_to_plan_limits(user)
        except Exception:
            logger.exception("Trim after revoke failed for %s", user.email)

        logger.warning(
            "Access revoked: %s -> %s (expiry=%s)",
            user.email, user.plan_tier, user.plan_expiry or "never",
        )

    def _grant(self, data: dict) -> None:
        """Grant access on a verified successful charge. Idempotent on the payment
        reference, so a re-sent webhook never double-grants or errors."""
        reference = data.get("reference", "")
        # Re-confirm the payment server-side before granting (guards against a
        # spoofed/replayed event moving a user to a paid tier for free).
        try:
            verified = verify_transaction(reference)
        except PaystackError as exc:
            logger.error("Paystack verify failed for %s: %s", reference, exc)
            return
        if verified.get("status") != "success":
            logger.warning("Paystack charge %s not successful: %s", reference, verified.get("status"))
            return

        # Trust the verified record over the webhook body for money-sensitive fields.
        metadata = verified.get("metadata") or data.get("metadata") or {}
        user = self._resolve_user({**verified, "metadata": metadata})
        if user is None:
            return

        plan = metadata.get("plan") or "pro"
        if plan not in PURCHASE_OPTIONS:
            plan = "pro"
        # Confirm the amount actually paid matches the plan we're about to grant.
        # Lifetime is priced well above Pro, so this also stops a Pro payment from
        # being replayed with lifetime metadata.
        expected = plan_amount_cents(plan)
        paid = int(verified.get("amount") or 0)
        if paid < expected:
            logger.warning(
                "Paystack %s: paid %s < expected %s for plan %s — not granting",
                reference, paid, expected, plan,
            )
            return

        lifetime = plan == LIFETIME
        granted = tier_granted_by(plan)  # lifetime -> pro
        tier = PlanTier.PRO if granted == PRO else PlanTier.STARTER
        # A null renewal/expiry is what marks access as permanent.
        renewal = None if lifetime else timezone.now() + timedelta(days=GRANT_DAYS)

        # Never demote someone who already owns lifetime — a later Starter/Pro
        # payment must not hand a permanent account a 31-day expiry.
        if is_lifetime(user) and not lifetime:
            renewal = None
            if plan_rank(user.plan_tier) > plan_rank(tier):
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
