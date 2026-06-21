from django.contrib import admin

from .models import Symbol


@admin.register(Symbol)
class SymbolAdmin(admin.ModelAdmin):
    list_display = ("ticker", "display_name", "hl_coin", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("ticker", "hl_coin", "display_name")
    list_editable = ("is_active", "sort_order")
