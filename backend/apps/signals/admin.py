from django.contrib import admin

from .models import (
    Signal,
    SignalDelivery,
    SignalService,
    StrategyVersion,
    TelegramDelivery,
    UserSignalSubscription,
)


@admin.register(SignalService)
class SignalServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "strategy_type", "is_active")
    list_filter = ("is_active", "strategy_type")
    list_editable = ("is_active",)  # toggle a strategy on/off right from the list
    prepopulated_fields = {"slug": ("name",)}


@admin.register(StrategyVersion)
class StrategyVersionAdmin(admin.ModelAdmin):
    """Read-only: a version is a historical fact. A change of rules mints a new one."""

    list_display = ("service", "number", "fingerprint_short", "created_at")
    list_filter = ("service",)
    readonly_fields = ("service", "number", "fingerprint", "code_hash", "snapshot", "created_at")

    @admin.display(description="fingerprint")
    def fingerprint_short(self, obj):
        return obj.fingerprint[:12]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = ("symbol", "service", "strategy_version", "direction", "confidence_pct", "timeframe", "outcome", "generated_at")
    list_filter = ("direction", "outcome", "service", "timeframe")
    list_select_related = ("symbol", "service", "strategy_version__service")
    search_fields = ("symbol__ticker",)
    date_hierarchy = "generated_at"


@admin.register(UserSignalSubscription)
class UserSignalSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "service", "subscribed_at")
    search_fields = ("user__email", "service__slug")


@admin.register(SignalDelivery)
class SignalDeliveryAdmin(admin.ModelAdmin):
    list_display = ("user", "signal", "delivered_at")
    search_fields = ("user__email",)
    date_hierarchy = "delivered_at"


@admin.register(TelegramDelivery)
class TelegramDeliveryAdmin(admin.ModelAdmin):
    list_display = ("user", "signal", "sent_at")
    search_fields = ("user__email",)
    date_hierarchy = "sent_at"
