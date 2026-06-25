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
from apps.common.email import send_payment_confirmation_email

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

    # Dodo event types (Standard Webhooks). `payment.succeeded` is what Dodo
    # actually fires on a successful subscription charge (initial + renewals) —
    # it's the event that grants/extends access. The subscription.* events are
    # kept for lifecycle changes (and in case the account also emits them).
    _GRANT = {"payment.succeeded", "subscription.active", "subscription.renewed"}
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

    def _resolve_plan(self, data, metadata):
        """Determine the plan from checkout metadata, falling back to the product
        id — which on a payment event may sit in a product_cart line item."""
        from .dodo import product_tier_map

        if metadata.get("plan"):
            return metadata["plan"]
        product_id = data.get("product_id")
        if not product_id:
            cart = data.get("product_cart") or []
            if cart:
                product_id = (cart[0] or {}).get("product_id")
        return product_tier_map().get(product_id, "pro")

    def _handle_event(self, event: dict) -> None:
        """Update billing state from a Dodo event. Idempotent on subscription_id."""
        from datetime import timedelta

        from django.utils.dateparse import parse_datetime

        event_type = event.get("type", "")
        data = event.get("data", {}) or {}
        user = self._resolve_user(event_type, data)
        if user is None:
            return

        if event_type in self._GRANT:
            metadata = data.get("metadata") or {}
            plan = self._resolve_plan(data, metadata)
            tier = PlanTier.STARTER if plan == "starter" else PlanTier.PRO
            next_billing = data.get("next_billing_date") or ""
            renewal = parse_datetime(next_billing) if next_billing else None
            if renewal is None:
                # payment.succeeded carries no billing date — default a month out
                # so access isn't indefinite; the next charge's event extends it.
                renewal = timezone.now() + timedelta(days=31)
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

            # Confirmation email only on the actual payment event — subscription.*
            # grant events can fire alongside it for the same purchase, and we
            # don't want to email the user twice for one charge.
            if event_type == "payment.succeeded":
                send_payment_confirmation_email(
                    to=user.email, plan_label=tier.label, renewal=renewal
                )

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
            # Drop saved watchlist symbols / chart layouts back to the Free caps
            # right away (the daily sweep catches silent lapses too).
            from apps.accounts.tasks import trim_to_plan_limits

            trim_to_plan_limits(user)
            logger.info("Webhook %s: %s downgraded to Free", event_type, user.email)

        else:
            logger.info("Unhandled Dodo event type: %s", event_type)
