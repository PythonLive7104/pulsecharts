"""Price alerts (Section 12 — v2 candidate).

A user sets an alert to fire when a symbol's price crosses above/below a target.
A cheap periodic task (check_price_alerts) compares active alerts against
Hyperliquid mid prices and triggers them; triggered alerts surface in-app.
One-shot: an alert deactivates once it fires.
"""

from django.conf import settings
from django.db import models

from apps.market_data.models import Symbol


class PriceAlert(models.Model):
    class Condition(models.TextChoices):
        ABOVE = "above", "Crosses above"
        BELOW = "below", "Crosses below"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="price_alerts"
    )
    symbol = models.ForeignKey(Symbol, on_delete=models.CASCADE, related_name="alerts")
    condition = models.CharField(max_length=8, choices=Condition.choices)
    target_price = models.FloatField()

    is_active = models.BooleanField(default=True, db_index=True)
    triggered_at = models.DateTimeField(null=True, blank=True)
    triggered_price = models.FloatField(null=True, blank=True)
    seen = models.BooleanField(default=False)  # in-app "unseen" badge

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.symbol.ticker} {self.condition} {self.target_price}"
