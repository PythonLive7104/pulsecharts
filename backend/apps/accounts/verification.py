"""Email-verification flow.

New signups start with ``email_verified=False`` and cannot obtain an auth token
until they click the emailed link — see ``CustomTokenObtainPairSerializer`` in
serializers.py, which is what actually enforces the gate at login.

The token/uid scheme mirrors the password-reset flow exactly (Django's
``default_token_generator`` + base64 uid), so links are single-use-ish and expire
on their own. No new model fields for the token: the generator derives it from the
user's state, and once ``email_verified`` flips, that state change invalidates the
token automatically.

Hard-required (per the rollout decision): if Resend is not configured the email
can't be sent and the user is stuck, so ``apps.signals``... no — the guard lives in
this app's ``AppConfig.ready`` and only WARNS (crashing prod would be worse than a
misconfigured email). The escape hatches are the resend endpoint and
``manage.py verify_user <email>``.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.common.email import EMAIL_ENABLED, send_verification_email

from .serializers import VerifiedTokenObtainPairSerializer


class VerifiedTokenObtainPairView(TokenObtainPairView):
    """Login endpoint that blocks unverified accounts (see the serializer)."""

    serializer_class = VerifiedTokenObtainPairSerializer

logger = logging.getLogger("accounts")
User = get_user_model()


def build_verify_link(user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_URL}/verify-email?uid={uid}&token={token}"


def send_verification(user) -> bool:
    """Email `user` a fresh verification link. Returns whether the email was sent.

    No-op (returns False) for an already-verified user — nothing to confirm.
    """
    if user.email_verified:
        return False
    link = build_verify_link(user)
    sent = send_verification_email(to=user.email, verify_link=link)
    # Always log the link server-side so the flow is recoverable if email delivery
    # is degraded (and testable locally where email is disabled).
    logger.info("Verification link for %s: %s", user.email, link)
    return sent


class VerifyEmailView(APIView):
    """POST /api/auth/verify-email/ {uid, token} — mark the account verified."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        uid = request.data.get("uid", "")
        token = request.data.get("token", "")
        try:
            pk = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=pk)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return Response(
                {"detail": "Invalid verification link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.email_verified:
            # Idempotent: clicking an old link after verifying (or twice) is a success,
            # not an error — the user's goal is already met.
            return Response({"detail": "Email already verified.", "verified": True})

        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "This verification link is invalid or has expired. "
                           "Request a new one below."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified", "email_verified_at"])
        return Response({"detail": "Email verified. You can now sign in.", "verified": True})


class ResendVerificationView(APIView):
    """POST /api/auth/verify-email/resend/ {email} — re-send the verification link.

    Always 200 with a generic message (never leaks which emails exist or which are
    already verified). Silently no-ops for unknown or already-verified addresses.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        generic = {
            "detail": "If that account exists and needs verification, "
                      "a new link is on its way.",
            "email_enabled": EMAIL_ENABLED,
        }
        if not email:
            return Response(generic)

        user = User.objects.filter(email__iexact=email).first()
        if user and not user.email_verified:
            send_verification(user)
        return Response(generic)
