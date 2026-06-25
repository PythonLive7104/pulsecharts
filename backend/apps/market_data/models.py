"""Market data models (Section 8).

Symbol is its own model (not hardcoded) so adding/removing tracked coins doesn't
require a deploy. Before locking the symbol list, confirm each ticker is actually
listed on Hyperliquid (Section 6.2 / 16 coverage caveat).
"""

from django.db import models


class Symbol(models.Model):
    class AssetClass(models.TextChoices):
        CRYPTO = "crypto", "Crypto"
        FOREX = "forex", "Forex"

    # Internal normalized ticker, e.g. "BTC-USD" / "EUR-USD" (Section 6.2).
    ticker = models.CharField(max_length=32, unique=True)
    # Which market this symbol belongs to. Drives the data feed (crypto =
    # Hyperliquid WS/REST; forex = Twelve Data REST), the UI Crypto/Forex toggle,
    # and price precision. Existing rows default to crypto.
    asset_class = models.CharField(
        max_length=8, choices=AssetClass.choices, default=AssetClass.CRYPTO
    )
    # Upstream Hyperliquid coin code used in the WS subscription, e.g. "BTC".
    # Used only for crypto symbols.
    hl_coin = models.CharField(max_length=32)
    # Provider-native symbol for non-Hyperliquid feeds, e.g. Twelve Data forex
    # "EUR/USD". Blank for crypto (hl_coin is the feed code there instead).
    feed_symbol = models.CharField(max_length=32, blank=True, default="")
    display_name = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "ticker"]

    def __str__(self):
        return self.ticker

    @property
    def is_forex(self) -> bool:
        return self.asset_class == self.AssetClass.FOREX

    @property
    def source_symbol(self) -> str:
        """The provider-native symbol to fetch/subscribe with: the forex feed
        symbol for forex, else the Hyperliquid coin code."""
        return self.feed_symbol if self.is_forex else self.hl_coin
