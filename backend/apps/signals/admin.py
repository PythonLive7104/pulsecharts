from django.contrib import admin

from .models import (
    Signal,
    SignalDelivery,
    SignalService,
    TelegramDelivery,
    UserSignalSubscription,
)


@admin.register(SignalService)
class SignalServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "strategy_type", "is_active")
    list_filter = ("is_active", "strategy_type")
    list_editable = ("is_active",)  # toggle a strategy on/off right from the list
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = ("symbol", "service", "direction", "confidence_pct", "timeframe", "outcome", "generated_at")
    list_filter = ("direction", "outcome", "service", "timeframe")
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
