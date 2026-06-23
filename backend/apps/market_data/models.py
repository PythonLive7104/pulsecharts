"""Market data models (Section 8).

Symbol is its own model (not hardcoded) so adding/removing tracked coins doesn't
require a deploy. Before locking the symbol list, confirm each ticker is actually
listed on Hyperliquid (Section 6.2 / 16 coverage caveat).
"""

from django.db import models


class Symbol(models.Model):
    # Internal normalized ticker, e.g. "BTC-USD" (Section 6.2).
    ticker = models.CharField(max_length=32, unique=True)
    # Upstream Hyperliquid coin code used in the WS subscription, e.g. "BTC".
    hl_coin = models.CharField(max_length=32)
    # Bybit V5 instrument used for auto-trade execution, e.g. "BTCUSDT". Blank
    # means this coin is charted/signalled but NOT auto-tradable on Bybit — the
    # execution engine skips signals whose symbol has no mapping.
    bybit_symbol = models.CharField(max_length=32, blank=True, default="")
    display_name = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "ticker"]

    def __str__(self):
        return self.ticker
