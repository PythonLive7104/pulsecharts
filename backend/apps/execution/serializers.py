"""Serializers for the auto-trade API.

Hard rule: the API NEVER returns a stored key or secret. The credential serializer
is write-only for secrets and exposes only whether keys are present + the risk
envelope. Execution history is read-only.
"""

from rest_framework import serializers

from .models import BrokerCredential, TradeExecution


class BrokerCredentialSerializer(serializers.ModelSerializer):
    # Secrets are write-only and optional on update: omit them to change only the
    # risk envelope / enabled flag without re-entering keys. Blank string clears.
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True,
                                    trim_whitespace=True)
    api_secret = serializers.CharField(write_only=True, required=False, allow_blank=True,
                                       trim_whitespace=True)
    # Read-only status the UI shows instead of the secrets.
    has_keys = serializers.BooleanField(read_only=True)
    effective_max_open_positions = serializers.IntegerField(read_only=True)
    effective_max_daily_trades = serializers.IntegerField(read_only=True)

    class Meta:
        model = BrokerCredential
        fields = (
            "broker", "testnet", "enabled",
            "margin_per_trade_usd", "max_open_positions", "max_daily_trades", "max_leverage",
            "api_key", "api_secret", "has_keys",
            "effective_max_open_positions", "effective_max_daily_trades",
            "created_at", "updated_at",
        )
        read_only_fields = ("has_keys", "created_at", "updated_at")

    def validate_margin_per_trade_usd(self, value):
        if value <= 0:
            raise serializers.ValidationError("Margin per trade must be positive.")
        return value

    def validate_max_leverage(self, value):
        if not (1 <= value <= 100):
            raise serializers.ValidationError("Max leverage must be between 1 and 100.")
        return value

    def _apply_secrets(self, instance, validated):
        # Pop the write-only secrets and route them through the encrypting setters.
        if "api_key" in validated:
            instance.set_api_key(validated.pop("api_key"))
        if "api_secret" in validated:
            instance.set_api_secret(validated.pop("api_secret"))

    def create(self, validated):
        from django.conf import settings

        user = self.context["request"].user
        # Seed the fixed per-trade margin from the platform default unless the user
        # set one explicitly (sizing is fixed-margin, not %-of-balance — see settings).
        instance = BrokerCredential(
            user=user, margin_per_trade_usd=settings.AUTO_TRADE_DEFAULT_MARGIN_USD
        )
        self._apply_secrets(instance, validated)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        return instance

    def update(self, instance, validated):
        self._apply_secrets(instance, validated)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        return instance

    def validate(self, attrs):
        # Can't switch on auto-trade without keys on file (or being provided now).
        enabling = attrs.get("enabled", getattr(self.instance, "enabled", False))
        if enabling:
            has_now = bool(attrs.get("api_key")) and bool(attrs.get("api_secret"))
            has_stored = bool(self.instance and self.instance.has_keys)
            if not (has_now or has_stored):
                raise serializers.ValidationError(
                    "Add your Bybit API key and secret before enabling auto-trade."
                )
        return attrs


class TradeExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradeExecution
        fields = (
            "id", "bybit_symbol", "direction", "timeframe", "status",
            "leverage", "qty", "notional", "margin",
            "entry_price", "stop_loss", "take_profit", "liq_price",
            "scaleout", "breakeven_moved",
            "bybit_order_id", "reason", "realized_pnl",
            "created_at", "closed_at",
        )
        read_only_fields = fields
