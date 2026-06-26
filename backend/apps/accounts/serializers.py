from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("id", "email", "password", "referral_code")

    def validate_referral_code(self, value):
        """Empty is fine (normal free signup); a non-empty code must be valid."""
        from .models import ReferralCode

        code = (value or "").strip().upper()
        if not code:
            return ""
        rc = ReferralCode.objects.filter(code=code).first()
        if rc is None or not rc.is_usable:
            raise serializers.ValidationError("That referral code is invalid or no longer available.")
        return code

    def create(self, validated_data):
        import logging

        from .models import ReferralCode
        from .onboarding import provision_default_setup

        code = validated_data.pop("referral_code", "")
        user = User.objects.create_user(**validated_data)
        if code:
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
