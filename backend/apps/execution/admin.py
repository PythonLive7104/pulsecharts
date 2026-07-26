"""Admin for the auto-trade executor. Secrets are never shown — only whether keys
are present — and executions are read-only audit rows."""

from django.contrib import admin

from .models import BrokerCredential, TradeExecution


@admin.register(BrokerCredential)
class BrokerCredentialAdmin(admin.ModelAdmin):
    list_display = ("user", "broker", "testnet", "enabled", "has_keys",
                    "margin_per_trade_usd", "max_leverage", "updated_at")
    list_filter = ("broker", "testnet", "enabled")
    search_fields = ("user__email",)
    readonly_fields = ("has_keys", "created_at", "updated_at")
    # The encrypted secret columns are deliberately excluded from the form so the
    # ciphertext is never rendered or editable in the admin.
    exclude = ("api_key_enc", "api_secret_enc")

    @admin.display(boolean=True, description="Keys on file")
    def has_keys(self, obj):
        return obj.has_keys


@admin.register(TradeExecution)
class TradeExecutionAdmin(admin.ModelAdmin):
    list_display = ("user", "direction", "bybit_symbol", "status", "leverage",
                    "qty", "notional", "scaleout", "breakeven_moved", "realized_pnl",
                    "created_at")
    list_filter = ("status", "direction", "scaleout", "breakeven_moved")
    search_fields = ("user__email", "bybit_symbol", "bybit_order_id", "order_link_id")
    readonly_fields = [f.name for f in TradeExecution._meta.fields]

    def has_add_permission(self, request):
        return False  # executions are created only by the executor
