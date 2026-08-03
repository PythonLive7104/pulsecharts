from django.contrib import admin

from .models import Symbol


@admin.register(Symbol)
class SymbolAdmin(admin.ModelAdmin):
    # signals_enabled is separate from is_active on purpose: is_active governs the
    # whole symbol (charting, search, watchlists), signals_enabled governs ONLY
    # whether the scan looks at it. See prune_signal_symbols.
    list_display = ("ticker", "display_name", "hl_coin", "asset_class", "is_active",
                    "signals_enabled", "sort_order")
    list_filter = ("is_active", "signals_enabled", "asset_class")
    search_fields = ("ticker", "hl_coin", "display_name")
    list_editable = ("is_active", "signals_enabled", "sort_order")
