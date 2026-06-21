from django.contrib import admin

from .models import ChartLayout


@admin.register(ChartLayout)
class ChartLayoutAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "symbol", "timeframe", "saved_at")
    search_fields = ("user__email", "symbol__ticker", "name")
