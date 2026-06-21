from rest_framework import serializers

from apps.market_data.models import Symbol

from .models import PriceAlert


class PriceAlertSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source="symbol.ticker", read_only=True)
    symbol_id = serializers.PrimaryKeyRelatedField(
        queryset=Symbol.objects.filter(is_active=True), source="symbol", write_only=True
    )

    class Meta:
        model = PriceAlert
        fields = (
            "id", "symbol", "symbol_id", "condition", "target_price",
            "is_active", "triggered_at", "triggered_price", "seen", "created_at",
        )
        read_only_fields = (
            "id", "is_active", "triggered_at", "triggered_price", "seen", "created_at",
        )

    def validate_target_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Target price must be positive.")
        return value
