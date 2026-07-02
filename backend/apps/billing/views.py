"""Billing endpoints (Section 9).

POST /api/billing/checkout/  — start a Paystack payment (auth required).
POST /api/billing/webhook/   — Paystack -> us, grants the plan on charge.success.
GET  /api/billing/history/   — the user's subscription records.

Billing model: one-time payments that grant 30 days of access (apps/billing/paystack.py).
Access lapses automatically at plan_expiry (accounts.plans.plan_key + the daily
trim sweep), so no cancel/renewal webhooks are needed.
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
        if plan not in {"starter", "pro"}:
            return Response(
                {"detail": "Choose a paid plan: 'starter' or 'pro'."},
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
        """Grant access on a verified successful charge. Idempotent on the payment
        reference, so a re-sent webhook never double-grants or errors."""
        event_type = event.get("event", "")
        data = event.get("data", {}) or {}

        if event_type != "charge.success":
            logger.info("Ignoring Paystack event: %s", event_type)
            return

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
        if plan not in {"starter", "pro"}:
            plan = "pro"
        # Confirm the amount actually paid matches the plan we're about to grant.
        expected = plan_amount_cents(plan)
        paid = int(verified.get("amount") or 0)
        if paid < expected:
            logger.warning(
                "Paystack %s: paid %s < expected %s for plan %s — not granting",
                reference, paid, expected, plan,
            )
            return

        tier = PlanTier.STARTER if plan == "starter" else PlanTier.PRO
        renewal = timezone.now() + timedelta(days=GRANT_DAYS)

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
        logger.info("Paystack charge.success: %s -> %s (expiry=%s)", user.email, tier, renewal)

        # Top the user's watchlist + followed strategies up to the new plan's
        # defaults (idempotent). Never let it break webhook processing.
        try:
            from apps.accounts.onboarding import provision_default_setup

            provision_default_setup(user)
        except Exception:
            logger.exception("Upgrade provisioning failed for %s", user.email)

        send_payment_confirmation_email(to=user.email, plan_label=tier.label, renewal=renewal)
