import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.email import send_password_reset_email
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
        from apps.accounts.plans import has_perpetual_access, is_lifetime_purchaser, plan_for

        user = request.user
        plan = plan_for(user)
        data = {
            "plan_tier": user.plan_tier,
            "plan_key": plan["key"],          # effective tier (expiry-aware)
            "plan_label": plan["label"],
            "plan_expiry": user.plan_expiry,  # null when access never expires
            "is_premium": user.is_premium,
            # Bought lifetime — hides the pricing sections. A staff-granted perpetual
            # Pro is NOT this, and still sees pricing.
            "is_lifetime": is_lifetime_purchaser(user),
            # Access never expires, however it was granted. Hides only the redeem
            # surfaces, which the API refuses for these users.
            "plan_never_expires": has_perpetual_access(user),
            **entitlements_for(plan["indicator_tiers"]),
            "signal_weekly_quota": plan["signal_weekly_quota"],  # Section 13.3 (-1 = unlimited)
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
        from apps.accounts.plans import LIFETIME_PLAN, PLANS

        # `lifetime` is served alongside — not inside — the tier list, since it's a
        # purchase option rather than a tier the pricing grid iterates over.
        return Response({"plans": list(PLANS.values()), "lifetime": LIFETIME_PLAN})


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

        from apps.accounts.plans import PLANS, PRO, STARTER, has_perpetual_access

        plan = request.data.get("plan")
        if plan not in (STARTER, PRO):
            return Response({"detail": "Choose 'starter' or 'pro'."}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        # A timed grant would replace a never-expiring plan's null expiry with a
        # date, silently downgrading them. Refuse before any credits are spent.
        if has_perpetual_access(user):
            return Response(
                {"detail": "Your plan doesn't expire — keep your credits for something else."},
                status=status.HTTP_400_BAD_REQUEST,
            )
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

        # Top watchlist + followed strategies up to the new plan's defaults
        # (idempotent; never block the redeem on a provisioning hiccup).
        try:
            from .onboarding import provision_default_setup

            provision_default_setup(user)
        except Exception:
            logger.exception("Upgrade provisioning failed for %s", user.email)

        return Response({
            "plan_tier": user.plan_tier,
            "plan_expiry": user.plan_expiry,
            "credits": user.referral_credits,
        })


class RedeemPromoCodeView(APIView):
    """POST /api/me/referral/redeem-code/ {code} — redeem an admin promo code.

    Grants the plan the entered code maps to for a fixed window so invited users
    can trial premium: settings.ADMIN_PRO_CODE → Pro (ADMIN_PRO_DAYS), or
    settings.ADMIN_STARTER_CODE → Starter (ADMIN_STARTER_DAYS). One redemption per
    user per code value (tracked separately, so a user may redeem each once);
    rotating a code opens a fresh window. Not tied to credits or the referral graph.
    """

    def post(self, request):
        from datetime import timedelta

        from django.utils import timezone

        from apps.accounts.plans import PRO, STARTER, has_perpetual_access, plan_key, plan_rank

        # Each configured code maps to (plan tier, grant days, the model field that
        # records prior redemption of THAT code). Ordered Pro-first so a value that
        # somehow matched both is treated as the higher grant.
        code_map = [
            (settings.ADMIN_PRO_CODE, PRO, settings.ADMIN_PRO_DAYS,
             "pro_promo_code_used", "Pro"),
            (settings.ADMIN_STARTER_CODE, STARTER, settings.ADMIN_STARTER_DAYS,
             "starter_promo_code_used", "Starter"),
        ]
        active = [(c.strip(), tier, days, field, label)
                  for c, tier, days, field, label in code_map if (c or "").strip()]
        if not active:
            return Response(
                {"detail": "No promo code is active right now."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entered = (request.data.get("code") or "").strip()
        if not entered:
            return Response({"detail": "Enter a code."}, status=status.HTTP_400_BAD_REQUEST)

        match = next((m for m in active if entered.upper() == m[0].upper()), None)
        if match is None:
            return Response({"detail": "That code isn't valid."}, status=status.HTTP_400_BAD_REQUEST)
        code, tier, days, field, label = match

        user = request.user
        # One grant per code value — don't let the same code be re-redeemed to stack days.
        if (getattr(user, field) or "").upper() == code.upper():
            return Response(
                {"detail": "You've already redeemed this code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Never downgrade: a user on an active higher tier redeeming a lower-tier
        # code (e.g. a Pro user entering the Starter code) keeps their plan. A
        # never-expiring plan outranks every timed grant, including an equal-tier
        # one, since applying it would swap a null expiry for a dated one.
        if has_perpetual_access(user):
            return Response(
                {"detail": "Your plan doesn't expire — no code needed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if plan_rank(plan_key(user)) > plan_rank(tier):
            return Response(
                {"detail": f"You're already on a higher plan than {label}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        # Extend from the current expiry if still active, otherwise start fresh.
        base = user.plan_expiry if (user.plan_expiry and user.plan_expiry > now) else now
        user.plan_tier = tier
        user.plan_expiry = base + timedelta(days=days)
        setattr(user, field, code)
        user.save(update_fields=["plan_tier", "plan_expiry", field])

        # Top watchlist + followed strategies up to the new plan's defaults (idempotent).
        try:
            from .onboarding import provision_default_setup

            provision_default_setup(user)
        except Exception:
            logger.exception("Promo upgrade provisioning failed for %s", user.email)

        logger.info("Promo code redeemed: %s -> %s until %s", user.email, label, user.plan_expiry)
        return Response({
            "plan_tier": user.plan_tier,
            "plan_expiry": user.plan_expiry,
            "days": days,
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
            "can_reconnect": user.telegram_can_reconnect,  # one-click, no deep link
            "is_premium": user.is_premium,
            "bot_username": settings.TELEGRAM_USERNAME,
            "link_url": None,
        }
        # Offer the deep link only when there's no remembered chat to reconnect to;
        # a remembered chat is reactivated via the one-click reconnect endpoint
        # instead (Telegram won't re-send /start for an already-started chat).
        if (
            telegram.is_configured()
            and user.is_premium
            and not user.telegram_connected
            and not user.telegram_can_reconnect
        ):
            data["link_url"] = telegram.deep_link(user.ensure_telegram_link_token())
        return Response(data)


class TelegramDisconnectView(APIView):
    """POST /api/me/telegram/disconnect/ — stop delivery, but remember the chat so
    the user can reconnect in one click (see TelegramReconnectView)."""

    def post(self, request):
        user = request.user
        user.telegram_active = False
        user.telegram_link_token = ""
        user.telegram_link_token_at = None
        user.save(update_fields=["telegram_active", "telegram_link_token", "telegram_link_token_at"])
        return Response({"connected": False, "can_reconnect": user.telegram_can_reconnect})


class TelegramReconnectView(APIView):
    """POST /api/me/telegram/reconnect/ — re-enable delivery to a remembered chat.

    No Telegram round-trip needed: the chat_id is still on file from before the
    disconnect, so flipping telegram_active back on resumes delivery immediately.
    If there's no remembered chat (never linked, or the user deleted the chat),
    the client should fall back to the deep link from the status endpoint.
    """

    def post(self, request):
        from apps.accounts import telegram

        user = request.user
        if not user.telegram_chat_id:
            return Response(
                {"detail": "No Telegram chat on file — use the connect link instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.telegram_active = True
        user.save(update_fields=["telegram_active"])
        telegram.send_message(
            user.telegram_chat_id,
            "✅ <b>Reconnected.</b> You'll receive PulseCharts signals here again.",
        )
        return Response({"connected": True})


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
            if not token:
                # Bare /start (no payload). If we still remember this chat from a
                # previous link, treat it as a reconnect; otherwise point them at
                # the dashboard to get a proper connect link.
                existing = User.objects.filter(telegram_chat_id=chat_id).first()
                if existing:
                    if not existing.telegram_active:
                        existing.telegram_active = True
                        existing.save(update_fields=["telegram_active"])
                    telegram.send_message(
                        chat_id,
                        "✅ <b>Reconnected.</b> You'll receive PulseCharts signals here again.",
                    )
                else:
                    telegram.send_message(
                        chat_id,
                        "Open PulseCharts → Signals → Connect Telegram to link your account.",
                    )
                return Response({"ok": True})
            user = User.objects.filter(telegram_link_token=token).first()
            if not user or not user.telegram_token_valid(token):
                # No match, or the link has expired (TTL) — a forwarded/stale link
                # lands here instead of binding someone else's account.
                telegram.send_message(
                    chat_id,
                    "I couldn't match that link, or it has expired. Open PulseCharts → "
                    "Signals → Connect Telegram to get a fresh link.",
                )
            elif user.telegram_chat_id and user.telegram_chat_id != chat_id:
                # Already linked to a different chat — don't silently hijack it.
                telegram.send_message(
                    chat_id,
                    "This PulseCharts account is already linked to another Telegram chat. "
                    "Disconnect it first in PulseCharts → Signals, then reconnect.",
                )
            else:
                user.telegram_chat_id = chat_id
                user.telegram_active = True
                user.telegram_link_token = ""  # one-time use
                user.telegram_link_token_at = None
                user.save(update_fields=[
                    "telegram_chat_id", "telegram_active",
                    "telegram_link_token", "telegram_link_token_at",
                ])
                telegram.send_message(
                    chat_id,
                    "✅ <b>Connected.</b> You'll now receive PulseCharts signals here. "
                    "Send /stop any time to unlink.\n\n"
                    "<i>Informational only. Not financial advice.</i>",
                )
        elif text.startswith("/stop"):
            # Switch delivery off but keep the chat on file, so the user can turn
            # it back on from the dashboard (or by sending /start) in one step.
            n = User.objects.filter(telegram_chat_id=chat_id, telegram_active=True).update(
                telegram_active=False
            )
            if n:
                telegram.send_message(
                    chat_id,
                    "🔕 Paused. You won't receive signals here. Send /start or reconnect "
                    "in PulseCharts → Signals to resume.",
                )

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
    The reset link is emailed via Resend (apps/common/email). It's also logged
    server-side, and surfaced in the response only when DEBUG is on so the flow
    stays testable locally without an email provider configured.
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

        send_password_reset_email(to=user.email, reset_link=reset_link)
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
