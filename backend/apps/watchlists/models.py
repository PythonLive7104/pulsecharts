"""Watchlist (Section 8, 12).

Free tier has a capped symbol count; premium expands it. The cap is enforced
server-side (see views), with the limits defined here.
"""

from django.conf import settings
from django.db import models

from apps.market_data.models import Symbol

# Section 12: free = capped watchlist, premium = expanded.
FREE_WATCHLIST_LIMIT = 10
PREMIUM_WATCHLIST_LIMIT = 100


class WatchlistItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watchlist_items",
    )
    symbol = models.ForeignKey(Symbol, on_delete=models.CASCADE)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        # A user can't add the same symbol twice.
        constraints = [
            models.UniqueConstraint(
                fields=["user", "symbol"], name="uniq_user_symbol_watchlist"
            )
        ]

    def __str__(self):
        return f"{self.user_id} · {self.symbol.ticker}"


def watchlist_limit_for(user) -> int:
    from apps.accounts.plans import plan_for

    return plan_for(user)["watchlist_limit"]
