import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.market_data.indicators import entitlements_for

from .serializers import (
    ChangePasswordSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)

logger = logging.getLogger("accounts")
User = get_user_model()


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — open signup."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    # JWT-only (no SessionAuthentication): a logged-in admin's session cookie in
    # the same browser would otherwise trigger DRF's CSRF enforcement on this
    # public POST and fail signup with "CSRF Failed". Matches the other public
    # auth endpoints below.
    authentication_classes = []


class EntitlementsView(APIView):
    """GET /api/me/entitlements/ (Section 9, 11).

    Returns the user's plan plus the indicator set unlocked for it. The frontend
    re-checks this each session to drive premium gating.
    """

    def get(self, request):
        from apps.accounts.plans import plan_for

        user = request.user
        plan = plan_for(user)
        data = {
            "plan_tier": user.plan_tier,
            "plan_key": plan["key"],          # effective tier (expiry-aware)
            "plan_label": plan["label"],
            "plan_expiry": user.plan_expiry,
            "is_premium": user.is_premium,
            **entitlements_for(plan["indicator_tiers"]),
            "signal_daily_quota": plan["signal_daily_quota"],  # Section 13.3 (-1 = unlimited)
            "strategies_allowed": plan["strategies"],
            "watchlist_limit": plan["watchlist_limit"],
            "layout_limit": plan["layout_limit"],
        }
        return Response(data)


class PlansView(APIView):
    """GET /api/plans/ — public plan catalog for the pricing/billing page."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        from apps.accounts.plans import PLANS

        return Response({"plans": list(PLANS.values())})


class ReferralView(APIView):
    """GET /api/me/referral/ — the user's personal code, share link, and earnings."""

    def get(self, request):
        from apps.accounts.plans import PLANS, PRO, STARTER

        user = request.user
        rc = user.ensure_referral_code()
        credits = user.referral_credits
        starter_price = PLANS[STARTER]["price_usd"]
        pro_price = PLANS[PRO]["price_usd"]
        return Response({
            "code": rc.code,
            "share_url": f"{settings.FRONTEND_URL}/signup?ref={rc.code}",
            "credits": credits,
            "referred_count": rc.used_count,
            "reward_per_referral": rc.REWARD_USD,
            "prices": {"starter": starter_price, "pro": pro_price},
            "can_redeem_starter": credits >= starter_price,
            "can_redeem_pro": credits >= pro_price,
        })


class ReferralSetCodeView(APIView):
    """POST /api/me/referral/code/ — set a custom personal referral code."""

    def post(self, request):
        import re

        from .models import ReferralCode

        code = (request.data.get("code") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{4,40}", code):
            return Response(
                {"detail": "Use 4–40 letters, numbers or underscores (e.g. MAILIONDEV_7788)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if ReferralCode.objects.filter(code=code).exclude(owner=request.user).exists():
            return Response({"detail": "That code is already taken."}, status=status.HTTP_400_BAD_REQUEST)
        rc = request.user.ensure_referral_code()
        rc.code = code
        rc.save()
        return Response({"code": rc.code})


class ReferralRedeemView(APIView):
    """POST /api/me/referral/redeem/ {plan: starter|pro} — spend credits on a plan."""

    def post(self, request):
        from datetime import timedelta

        from django.utils import timezone

        from apps.accounts.plans import PLANS, PRO, STARTER

        plan = request.data.get("plan")
        if plan not in (STARTER, PRO):
            return Response({"detail": "Choose 'starter' or 'pro'."}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        price = PLANS[plan]["price_usd"]
        if user.referral_credits < price:
            return Response(
                {"detail": f"You need ${price} in credits for {plan.title()} — you have ${user.referral_credits}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        # Extend from the current expiry if still active, otherwise start fresh.
        base = user.plan_expiry if (user.plan_expiry and user.plan_expiry > now) else now
        user.referral_credits -= price
        user.plan_tier = plan
        user.plan_expiry = base + timedelta(days=30)
        user.save(update_fields=["referral_credits", "plan_tier", "plan_expiry"])
        return Response({
            "plan_tier": user.plan_tier,
            "plan_expiry": user.plan_expiry,
            "credits": user.referral_credits,
        })


class TelegramStatusView(APIView):
    """GET /api/me/telegram/ — connection status + deep link to connect.

    Telegram signal delivery is a premium feature, so the connect link is only
    offered to premium users; everyone can see their status.
    """

    def get(self, request):
        from apps.accounts import telegram

        user = request.user
        data = {
            "configured": telegram.is_configured(),  # bot set up server-side at all
            "connected": user.telegram_connected,
            "is_premium": user.is_premium,
            "bot_username": settings.TELEGRAM_USERNAME,
            "link_url": None,
        }
        if telegram.is_configured() and user.is_premium and not user.telegram_connected:
            data["link_url"] = telegram.deep_link(user.ensure_telegram_link_token())
        return Response(data)


class TelegramDisconnectView(APIView):
    """POST /api/me/telegram/disconnect/ — unlink the user's Telegram."""

    def post(self, request):
        user = request.user
        user.telegram_chat_id = ""
        user.telegram_link_token = ""
        user.save(update_fields=["telegram_chat_id", "telegram_link_token"])
        return Response({"connected": False})


class TelegramWebhookView(APIView):
    """POST /api/telegram/webhook/<secret>/ — Telegram -> us.

    Public endpoint authenticated only by the secret in the path (so random
    callers can't drive it). Handles the bot's /start <token> deep link to link a
    chat to a user, and /stop to unlink.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, secret):
        from apps.accounts import telegram

        if not settings.TELEGRAM_WEBHOOK_SECRET or secret != settings.TELEGRAM_WEBHOOK_SECRET:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

        msg = (request.data or {}).get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            return Response({"ok": True})  # ignore non-message updates

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            token = parts[1].strip() if len(parts) > 1 else ""
            user = User.objects.filter(telegram_link_token=token).first() if token else None
            if user:
                user.telegram_chat_id = chat_id
                user.telegram_link_token = ""  # one-time use
                user.save(update_fields=["telegram_chat_id", "telegram_link_token"])
                telegram.send_message(
                    chat_id,
                    "✅ <b>Connected.</b> You'll now receive PulseCharts signals here. "
                    "Send /stop any time to unlink.\n\n"
                    "<i>Informational only. Not financial advice.</i>",
                )
            else:
                telegram.send_message(
                    chat_id,
                    "I couldn't match that link. Open PulseCharts → Signals → Connect "
                    "Telegram to get a fresh link.",
                )
        elif text.startswith("/stop"):
            n = User.objects.filter(telegram_chat_id=chat_id).update(telegram_chat_id="")
            if n:
                telegram.send_message(chat_id, "🔕 Unlinked. You won't receive signals here anymore.")

        return Response({"ok": True})


class MeView(generics.RetrieveAPIView):
    """GET /api/me/ — current user profile."""

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    """POST /api/me/change-password/ — change while authenticated."""

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["old_password"]):
            return Response(
                {"detail": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password updated."})


class PasswordResetRequestView(APIView):
    """POST /api/auth/password-reset/ — start a reset.

    Always returns 200 with a generic message (never leak which emails exist).
    Email delivery isn't wired yet (no transactional provider chosen — Section
    13.7), so for now the reset link is logged server-side, and surfaced in the
    response only when DEBUG is on so the flow is testable in dev.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        generic = {"detail": "If that email exists, a reset link has been sent."}
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(generic)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"

        # TODO (Section 13.7): send via a transactional email provider once chosen.
        logger.info("Password reset link for %s: %s", email, reset_link)

        if settings.DEBUG:
            return Response({**generic, "debug_reset_link": reset_link})
        return Response(generic)


class PasswordResetConfirmView(APIView):
    """POST /api/auth/password-reset/confirm/ — set a new password."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            uid = force_str(urlsafe_base64_decode(data["uid"]))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return Response(
                {"detail": "Invalid reset link."}, status=status.HTTP_400_BAD_REQUEST
            )

        if not default_token_generator.check_token(user, data["token"]):
            return Response(
                {"detail": "Reset link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(data["password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password has been reset."})
