from rest_framework import serializers

from apps.market_data.models import Symbol
from apps.market_data.serializers import SymbolSerializer

from .models import WatchlistItem


class WatchlistItemSerializer(serializers.ModelSerializer):
    symbol = SymbolSerializer(read_only=True)
    symbol_id = serializers.PrimaryKeyRelatedField(
        queryset=Symbol.objects.filter(is_active=True),
        source="symbol",
        write_only=True,
    )

    class Meta:
        model = WatchlistItem
        fields = ("id", "symbol", "symbol_id", "sort_order", "created_at")
        read_only_fields = ("id", "created_at")
