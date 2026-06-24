from rest_framework import serializers

from .models import Signal, SignalService, UserSignalSubscription


class SignalServiceSerializer(serializers.ModelSerializer):
    is_followed = serializers.SerializerMethodField()

    class Meta:
        model = SignalService
        fields = ("id", "name", "slug", "description", "strategy_type", "is_followed")

    def get_is_followed(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.subscribers.filter(user=request.user).exists()


class SubscriptionSerializer(serializers.ModelSerializer):
    service = SignalServiceSerializer(read_only=True)
    service_id = serializers.PrimaryKeyRelatedField(
        queryset=SignalService.objects.filter(is_active=True),
        source="service",
        write_only=True,
    )

    class Meta:
        model = UserSignalSubscription
        fields = ("id", "service", "service_id", "subscribed_at")
        read_only_fields = ("id", "subscribed_at")


class SignalSerializer(serializers.ModelSerializer):
    """Full signal card (Section 19.1)."""

    symbol = serializers.CharField(source="symbol.ticker", read_only=True)
    asset_class = serializers.CharField(source="symbol.asset_class", read_only=True)
    strategy = serializers.CharField(source="service.name", read_only=True)
    strategy_slug = serializers.CharField(source="service.slug", read_only=True)

    class Meta:
        model = Signal
        exclude = ("service", "created_at")
