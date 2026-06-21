"""Billing endpoints (Section 9).

POST /api/billing/checkout/  — create a Dodo checkout session (auth required).
POST /api/billing/webhook/   — Dodo -> us, updates Subscription + plan_tier.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import PlanTier, Subscription

from .dodo import DodoError, create_checkout_session, verify_webhook_signature

logger = logging.getLogger("billing")


class CheckoutView(APIView):
    """Start a premium subscription checkout."""

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
        except (DodoError, NotImplementedError) as exc:
            # Premium not live yet (Section 16) — surface a clean "coming soon".
            return Response(
                {"detail": str(exc), "billing_live": False},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(session, status=status.HTTP_201_CREATED)


class WebhookView(APIView):
    """Dodo Payments webhook. Public endpoint, authenticated by signature."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.headers.get("X-Dodo-Signature", "")
        if not verify_webhook_signature(request.body, signature):
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
        return Response({"received": True})

    def _handle_event(self, event: dict) -> None:
        """Update billing state from a Dodo event.

        TODO: map Dodo's real event types/fields. Sketched for the common cases:
        subscription activated/renewed -> grant premium; canceled/expired ->
        revoke. Idempotency is keyed on payment_ref.
        """
        from apps.accounts.models import User

        event_type = event.get("type", "")
        data = event.get("data", {})
        user_id = (data.get("metadata") or {}).get("user_id")
        if not user_id:
            logger.warning("Webhook %s missing user_id metadata", event_type)
            return

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logger.warning("Webhook %s references unknown user %s", event_type, user_id)
            return

        if event_type in {"subscription.active", "subscription.renewed"}:
            renewal = data.get("renewal_date")
            # Which plan was purchased — carried in checkout metadata.
            plan = (data.get("metadata") or {}).get("plan", "pro")
            tier = PlanTier.STARTER if plan == "starter" else PlanTier.PRO
            Subscription.objects.update_or_create(
                user=user,
                payment_ref=data.get("id", ""),
                defaults={
                    "tier": tier,
                    "status": Subscription.Status.ACTIVE,
                    "renewal_date": renewal,
                },
            )
            user.plan_tier = tier
            user.plan_expiry = renewal
            if data.get("customer_id"):
                user.dodo_customer_id = data["customer_id"]
            user.save(update_fields=["plan_tier", "plan_expiry", "dodo_customer_id"])

        elif event_type in {"subscription.canceled", "subscription.expired"}:
            Subscription.objects.filter(user=user).update(
                status=Subscription.Status.CANCELED
            )
            user.plan_tier = PlanTier.FREE
            user.plan_expiry = timezone.now()
            user.save(update_fields=["plan_tier", "plan_expiry"])
        else:
            logger.info("Unhandled Dodo event type: %s", event_type)
