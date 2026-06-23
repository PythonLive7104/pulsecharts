"""Serializers for the auto-trade API.

Credentials are write-only — they go in on connect and are never serialized back
out (we only ever expose connection status, never the key/secret).
"""

from rest_framework import serializers

from .models import AutoTradeConfig, BrokerConnection, TradeExecution


class BrokerConnectSerializer(serializers.Serializer):
    """Input for POST /api/me/broker/ — connect/replace credentials."""

    api_key = serializers.CharField(write_only=True, trim_whitespace=True)
    api_secret = serializers.CharField(write_only=True, trim_whitespace=True)
    testnet = serializers.BooleanField(default=True)
    # Explicit acknowledgement that the user authorizes automated execution on
    # their own account. Required — this is the consent record.
    authorize = serializers.BooleanField(write_only=True)

    def validate_authorize(self, value):
        if not value:
            raise serializers.ValidationError(
                "You must authorize automated trade execution to connect a broker."
            )
        return value


class BrokerConnectionSerializer(serializers.ModelSerializer):
    """Read-only connection status. Never exposes credentials."""

    class Meta:
        model = BrokerConnection
        fields = [
            "broker", "testnet", "status", "permission_verified",
            "last_error", "last_checked_at", "created_at",
        ]
        read_only_fields = fields


class AutoTradeConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutoTradeConfig
        fields = [
            "enabled", "sizing_mode", "risk_pct", "fixed_usd", "pct_balance",
            "leverage", "max_open_positions", "max_daily_trades",
            "tp_distribution", "move_sl_to_be_after_tp",
            "max_slippage_pct", "max_signal_age_sec", "min_confidence",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_tp_distribution(self, value):
        if not value:
            return value
        if len(value) != 4:
            raise serializers.ValidationError("tp_distribution must have exactly 4 values (TP1–TP4).")
        if any(v < 0 for v in value):
            raise serializers.ValidationError("tp_distribution values cannot be negative.")
        if round(sum(value), 4) != 100:
            raise serializers.ValidationError("tp_distribution must sum to 100.")
        return value

    def validate_leverage(self, value):
        if not 1 <= value <= 50:
            raise serializers.ValidationError("leverage must be between 1 and 50.")
        return value

    def validate_risk_pct(self, value):
        if not 0 < value <= 20:
            raise serializers.ValidationError("risk_pct must be between 0 and 20.")
        return value


class TradeExecutionSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source="signal.symbol.ticker", read_only=True)
    strategy = serializers.CharField(source="signal.service.name", read_only=True)
    confidence_pct = serializers.IntegerField(source="signal.confidence_pct", read_only=True)
    timeframe = serializers.CharField(source="signal.timeframe", read_only=True)

    class Meta:
        model = TradeExecution
        fields = [
            "id", "symbol", "strategy", "confidence_pct", "timeframe",
            "status", "detail", "side", "bybit_symbol", "qty", "leverage",
            "intended_entry", "fill_price", "realized_pnl", "close_reason",
            "closed_at", "created_at",
        ]
        read_only_fields = fields
