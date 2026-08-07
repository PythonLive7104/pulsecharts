from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class VerifiedTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login serializer that refuses tokens to unverified accounts.

    The gate lives HERE (at token issuance) rather than as a UI redirect: an
    unverified user never receives an access token, so there is no partial-access
    surface to protect. Credentials are checked first (via super().validate) so this
    never reveals whether an email exists — only a *correct* login on an unverified
    account gets the verification message; a wrong password still just fails auth.

    The distinctive `code` lets the frontend show a "resend verification" affordance
    instead of a generic error.
    """

    def validate(self, attrs):
        data = super().validate(attrs)  # raises on bad credentials before we get here
        if not self.user.email_verified:
            raise serializers.ValidationError(
                {
                    "detail": "Please verify your email address before signing in. "
                              "Check your inbox for the verification link.",
                    "code": "email_not_verified",
                    "email": self.user.email,
                }
            )
        return data


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("id", "email", "password", "referral_code")

    def validate_referral_code(self, value):
        """Empty is fine (normal free signup); a non-empty code must be valid.

        Accepts EITHER kind of code, because a user handed "a code" types it into the
        first field they see and has no idea we keep two registries:
          * a referral code (ReferralCode row) — grants Starter and credits its owner;
          * an admin access code (ADMIN_PRO_CODE / ADMIN_STARTER_CODE) — grants the
            plan directly.
        Previously an access code failed here with "invalid or no longer available",
        which reads as a dead code rather than "right code, wrong box".
        """
        from . import promo
        from .models import ReferralCode

        code = (value or "").strip().upper()
        if not code:
            return ""
        if promo.match(code):
            return code
        rc = ReferralCode.objects.filter(code=code).first()
        if rc is None or not rc.is_usable:
            raise serializers.ValidationError("That referral code is invalid or no longer available.")
        return code

    def create(self, validated_data):
        import logging

        from .models import ReferralCode
        from .onboarding import provision_default_setup

        from . import promo

        code = validated_data.pop("referral_code", "")
        user = User.objects.create_user(**validated_data)
        if code:
            matched = promo.match(code)
            if matched:
                # An admin access code. Same rules as the billing-page redemption
                # (apps.accounts.promo) — every check is vacuous for a brand-new
                # account, but sharing the code path keeps the two from drifting.
                # Deliberately does NOT set referred_by_code: an admin code has no
                # owner, so there is nobody to credit or pay commission to.
                try:
                    promo.check_redeemable(user, matched)
                    promo.apply_grant(user, matched)
                except promo.PromoError:
                    pass  # can't happen on a fresh account; never fail signup over it
            else:
                rc = ReferralCode.objects.filter(code=code).first()
                if rc and rc.is_usable:  # re-check (race-safe enough at this scale)
                    rc.redeem(user)  # may upgrade plan_tier, so provision *after* this
        # Seed a default watchlist + followed strategies sized by the user's plan
        # so they don't land on an empty dashboard. Never let this break signup.
        try:
            provision_default_setup(user)
        except Exception:
            logging.getLogger("accounts").exception(
                "Default provisioning failed for new user %s", user.pk
            )
        # Send the email-verification link. New users are unverified (model default)
        # and can't obtain a token until they click it. Never let a send failure break
        # signup — the account still exists and the user can hit "resend".
        try:
            from .verification import send_verification

            send_verification(user)
        except Exception:
            logging.getLogger("accounts").exception(
                "Verification email failed for new user %s", user.pk
            )
        return user


class UserSerializer(serializers.ModelSerializer):
    is_premium = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "plan_tier", "plan_expiry", "is_premium")
        read_only_fields = fields


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(min_length=8)


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8)
