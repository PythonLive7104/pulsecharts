from django.contrib import admin

from .models import AutoTradeConfig, BrokerConnection, TradeExecution


@admin.register(BrokerConnection)
class BrokerConnectionAdmin(admin.ModelAdmin):
    list_display = ("user", "broker", "testnet", "status", "permission_verified", "last_checked_at")
    list_filter = ("broker", "status", "testnet", "permission_verified")
    search_fields = ("user__email",)
    # Never surface the encrypted credentials in the admin form.
    exclude = ("api_key_enc", "api_secret_enc")
    readonly_fields = ("status", "permission_verified", "last_error", "last_checked_at",
                       "created_at", "updated_at")


@admin.register(AutoTradeConfig)
class AutoTradeConfigAdmin(admin.ModelAdmin):
    list_display = ("user", "enabled", "sizing_mode", "risk_pct", "leverage",
                    "max_open_positions", "max_daily_trades")
    list_filter = ("enabled", "sizing_mode")
    search_fields = ("user__email",)


@admin.register(TradeExecution)
class TradeExecutionAdmin(admin.ModelAdmin):
    list_display = ("user", "signal", "status", "side", "bybit_symbol", "qty",
                    "fill_price", "realized_pnl", "close_reason", "created_at")
    list_filter = ("status", "close_reason")
    search_fields = ("user__email", "bybit_symbol")
    date_hierarchy = "created_at"
