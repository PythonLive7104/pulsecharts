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
    has_perpetual_access,
    is_lifetime_purchaser,
    plan_key,
    plan_rank,
    tier_granted_by,
)
from apps.accounts.tasks import trim_to_plan_limits
from apps.common.email import (
    send_payment_admin_alert,
    send_payment_confirmation_email,
)

from .paystack import (
    PaystackError,
    create_checkout_session,
    plan_amount_cents,
    verify_transaction,
    verify_webhook_signature,
)
from .grant import GRANT_DAYS, apply_paid_grant
from .serializers import SubscriptionSerializer

logger = logging.getLogger("billing")

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
        # Nothing left to sell an existing lifetime buyer.
        if is_lifetime_purchaser(request.user):
            return Response(
                {"detail": "You're on the lifetime plan — no further payment needed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # A staff-granted perpetual plan may still be converted into a real lifetime
        # purchase, but never into a timed one — that would write an expiry onto
        # access that currently has none, downgrading them for money.
        if has_perpetual_access(request.user) and plan != LIFETIME:
            return Response(
                {"detail": "Your plan doesn't expire — a monthly plan would replace it, not extend it."},
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
        # You don't owe a share of revenue that was handed back.
        self._void_commission(reference, new_status.lower())
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

    @staticmethod
    def _void_commission(reference: str, reason: str) -> None:
        """Void the commission attached to a refunded/disputed charge — you don't owe
        a share of revenue you had to give back. Paid-out rows are left alone and
        logged loudly: that money is already gone and needs a human decision."""
        from apps.accounts.models import ReferralCommission

        row = ReferralCommission.objects.filter(payment_ref=reference).first()
        if row is None:
            return
        if row.status == ReferralCommission.Status.PAID:
            logger.warning(
                "Commission on %s was ALREADY PAID OUT ($%s to %s) but the charge was "
                "%s — recover it manually.", reference, row.commission_usd,
                row.referrer.email, reason,
            )
            return
        row.status = ReferralCommission.Status.VOID
        row.payout_note = (f"{row.payout_note} · voided: {reason}").strip(" ·")
        row.save(update_fields=["status", "payout_note"])
        logger.info("Commission voided on %s (%s)", reference, reason)

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

        apply_paid_grant(user, plan, reference, paid)


class CryptoWalletsView(APIView):
    """GET /api/billing/crypto/wallets/ — payable wallets + prices.

    Returns the QR as inline SVG so the frontend needs no QR library. Assets with no
    configured address are omitted rather than shown empty.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .crypto import wallets

        return Response({
            "wallets": wallets(),
            "plans": [
                {"key": k, "label": o["label"], "price_usd": o["price_usd"]}
                for k, o in PURCHASE_OPTIONS.items()
            ],
            "note": (
                "Send the exact amount, then submit the transaction hash below. "
                "Your plan is activated once we confirm the payment on-chain."
            ),
        })


class CryptoPaymentView(APIView):
    """POST /api/billing/crypto/claim/  — submit a payment claim
    GET  /api/billing/crypto/claim/  — this user's claims, newest first

    Submitting does NOT grant anything. A human checks the chain and approves in the
    admin — see apps/billing/models.CryptoPayment for why that is deliberate.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .models import CryptoPayment

        rows = CryptoPayment.objects.filter(user=request.user)[:20]
        return Response([
            {
                "id": r.id, "plan": r.plan, "asset": r.asset,
                "amount_usd": str(r.amount_usd), "tx_hash": r.tx_hash,
                "status": r.status, "status_label": r.get_status_display(),
                "created_at": r.created_at.isoformat(),
                "review_note": r.review_note,
            }
            for r in rows
        ])

    def post(self, request):
        from decimal import Decimal

        from .crypto import address_for
        from .models import CryptoPayment

        plan = (request.data.get("plan") or "").strip()
        asset = (request.data.get("asset") or "").strip().upper()
        tx_hash = (request.data.get("tx_hash") or "").strip()

        if plan not in PURCHASE_OPTIONS:
            return Response({"detail": "Unknown plan."}, status=status.HTTP_400_BAD_REQUEST)
        address = address_for(asset)
        if not address:
            return Response(
                {"detail": "That asset isn't accepted right now."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(tx_hash) < 10:
            return Response(
                {"detail": "Enter the transaction hash from your wallet."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # The hash is the receipt. Rejecting duplicates here — rather than at review —
        # stops one payment being claimed twice, including by a different account.
        from .models import CryptoPayment as CP

        if CP.objects.filter(asset=asset, tx_hash__iexact=tx_hash).exists():
            return Response(
                {"detail": "That transaction has already been submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        row = CryptoPayment.objects.create(
            user=request.user, plan=plan, asset=asset, address=address,
            # Price is frozen at claim time so a later price change cannot make an
            # already-paid claim look short.
            amount_usd=Decimal(str(PURCHASE_OPTIONS[plan]["price_usd"])),
            tx_hash=tx_hash,
            note=(request.data.get("note") or "").strip()[:500],
        )
        return Response(
            {"id": row.id, "status": row.status, "detail": "Submitted for review."},
            status=status.HTTP_201_CREATED,
        )
