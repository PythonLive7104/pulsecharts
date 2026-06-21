"""Saved chart layouts (Section 8, 12).

A layout is symbol + timeframe + indicator_config (JSON). Section 12: saving
*multiple* layouts is a premium feature, so the free tier is capped at one.
"""

from django.conf import settings
from django.db import models

from apps.market_data.models import Symbol

# Section 12: premium unlocks "multiple saved chart layouts".
FREE_LAYOUT_LIMIT = 1
PREMIUM_LAYOUT_LIMIT = 50


class ChartLayout(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chart_layouts",
    )
    name = models.CharField(max_length=120, blank=True, default="")
    symbol = models.ForeignKey(Symbol, on_delete=models.CASCADE)
    timeframe = models.CharField(max_length=8, default="1m")
    # Indicator slugs + their params, e.g. {"ema": {"period": 21}, "rsi": {...}}.
    indicator_config = models.JSONField(default=dict, blank=True)
    saved_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-saved_at"]

    def __str__(self):
        return f"{self.user_id} · {self.symbol.ticker} · {self.timeframe}"


def layout_limit_for(user) -> int:
    from apps.accounts.plans import plan_for

    return plan_for(user)["layout_limit"]


class Workspace(models.Model):
    """The user's current multi-chart workspace, synced across devices.

    One per user. `data` holds the serialized workspace snapshot (panes with
    symbol/timeframe/indicators/params/drawings + layout) — the same shape the
    frontend persists to localStorage, minus candle data.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workspace"
    )
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Workspace<{self.user_id}>"
