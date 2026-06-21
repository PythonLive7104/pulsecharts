from django.contrib import admin

from .models import WatchlistItem


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "symbol", "sort_order", "created_at")
    search_fields = ("user__email", "symbol__ticker")
