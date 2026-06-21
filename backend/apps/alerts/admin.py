from django.contrib import admin

from .models import PriceAlert


@admin.register(PriceAlert)
class PriceAlertAdmin(admin.ModelAdmin):
    list_display = ("user", "symbol", "condition", "target_price", "is_active", "triggered_at")
    list_filter = ("is_active", "condition")
    search_fields = ("user__email", "symbol__ticker")
