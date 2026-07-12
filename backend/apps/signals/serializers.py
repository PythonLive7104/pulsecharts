from rest_framework import serializers

from .models import Signal, SignalService, UserSignalSubscription


class SignalServiceSerializer(serializers.ModelSerializer):
    is_followed = serializers.SerializerMethodField()
    is_custom = serializers.SerializerMethodField()
    rule_summary = serializers.SerializerMethodField()

    class Meta:
        model = SignalService
        fields = (
            "id", "name", "slug", "description", "strategy_type",
            "is_followed", "is_custom", "rule_summary",
        )

    def get_is_followed(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.subscribers.filter(user=request.user).exists()

    def get_is_custom(self, obj):
        return obj.owner_id is not None

    def get_rule_summary(self, obj):
        if not obj.rule_config:
            return ""
        from .strategy_builder import rule_summary
        return rule_summary(obj.rule_config)


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
    # Confluence (delivery-side): how many distinct strategies agree on this call,
    # and their names. Defaults to just this signal's own strategy when the view
    # hasn't annotated confluence (e.g. resolved history).
    confluence_count = serializers.SerializerMethodField()
    confluence_services = serializers.SerializerMethodField()

    class Meta:
        model = Signal
        exclude = ("service", "created_at")

    def get_confluence_count(self, obj):
        return getattr(obj, "confluence_count", 1)

    def get_confluence_services(self, obj):
        return getattr(obj, "confluence_services", None) or [obj.service.name]
