"""Billing endpoints (Section 9).

POST /api/billing/checkout/  — create a Dodo checkout session (auth required).
POST /api/billing/webhook/   — Dodo -> us, updates Subscription + plan_tier.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import PlanTier, Subscription

from .dodo import DodoError, create_checkout_session, verify_webhook_signature
from .serializers import SubscriptionSerializer

logger = logging.getLogger("billing")


class SubscriptionHistoryView(ListAPIView):
    """GET /api/billing/history/ — the authenticated user's subscription records,
    newest first (model Meta already orders by -created_at)."""

    serializer_class = SubscriptionSerializer

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)


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
        return Response({"received": True})

    # Dodo subscription event types (Standard Webhooks).
    _GRANT = {"subscription.active", "subscription.renewed"}
    _CANCEL = {"subscription.cancelled", "subscription.canceled"}
    _REVOKE = {"subscription.expired", "subscription.failed", "subscription.on_hold"}

    def _resolve_user(self, event_type, data):
        """Link an event to a user via checkout metadata, falling back to email."""
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
        logger.warning("Webhook %s: could not resolve user (user_id=%s)", event_type, user_id)
        return None

    def _handle_event(self, event: dict) -> None:
        """Update billing state from a Dodo event. Idempotent on subscription_id."""
        from django.utils.dateparse import parse_datetime

        from .dodo import product_tier_map

        event_type = event.get("type", "")
        data = event.get("data", {}) or {}
        user = self._resolve_user(event_type, data)
        if user is None:
            return

        if event_type in self._GRANT:
            metadata = data.get("metadata") or {}
            plan = metadata.get("plan") or product_tier_map().get(data.get("product_id"), "pro")
            tier = PlanTier.STARTER if plan == "starter" else PlanTier.PRO
            next_billing = data.get("next_billing_date") or ""
            renewal = parse_datetime(next_billing) if next_billing else None
            ref = data.get("subscription_id") or data.get("payment_id") or ""
            customer_id = (data.get("customer") or {}).get("customer_id", "")

            Subscription.objects.update_or_create(
                user=user,
                payment_ref=ref,
                defaults={
                    "tier": tier,
                    "status": Subscription.Status.ACTIVE,
                    "renewal_date": renewal,
                },
            )
            user.plan_tier = tier
            user.plan_expiry = renewal
            if customer_id:
                user.dodo_customer_id = customer_id
            user.save(update_fields=["plan_tier", "plan_expiry", "dodo_customer_id"])
            logger.info("Webhook %s: %s -> %s (expiry=%s)", event_type, user.email, tier, renewal)

        elif event_type in self._CANCEL:
            # Cancelled but not yet expired: keep access until the paid period
            # ends — plan_key() drops them to Free automatically at plan_expiry.
            Subscription.objects.filter(user=user).update(status=Subscription.Status.CANCELED)
            logger.info("Webhook %s: %s subscription cancelled (access until expiry)", event_type, user.email)

        elif event_type in self._REVOKE:
            Subscription.objects.filter(user=user).update(status=Subscription.Status.EXPIRED)
            user.plan_tier = PlanTier.FREE
            user.plan_expiry = timezone.now()
            user.save(update_fields=["plan_tier", "plan_expiry"])
            logger.info("Webhook %s: %s downgraded to Free", event_type, user.email)

        else:
            logger.info("Unhandled Dodo event type: %s", event_type)
