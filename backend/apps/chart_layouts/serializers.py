from rest_framework import serializers

from apps.market_data.models import Symbol
from apps.market_data.serializers import SymbolSerializer

from .models import ChartLayout, Workspace


class ChartLayoutSerializer(serializers.ModelSerializer):
    symbol = SymbolSerializer(read_only=True)
    symbol_id = serializers.PrimaryKeyRelatedField(
        queryset=Symbol.objects.filter(is_active=True),
        source="symbol",
        write_only=True,
    )

    class Meta:
        model = ChartLayout
        fields = (
            "id",
            "name",
            "symbol",
            "symbol_id",
            "timeframe",
            "indicator_config",
            "saved_at",
        )
        read_only_fields = ("id", "saved_at")


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("data", "updated_at")
        read_only_fields = ("updated_at",)
