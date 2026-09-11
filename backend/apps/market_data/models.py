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

    class MinPlan(models.TextChoices):
        FREE = "free", "Free"
        STARTER = "starter", "Starter"
        PRO = "pro", "Pro"

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
    # Scan this symbol for trading signals? Separate from is_active on purpose: a
    # symbol can be perfectly good to CHART and to keep in a watchlist while being a
    # poor fit for the strategies — thin books wick through a 3-4.5xATR stop that
    # sits outside normal noise on a major, so the same setup that works on BTC
    # bleeds on a low-float alt. Turning it off removes the symbol from the signal
    # scan ONLY; charts, search and watchlists are untouched.
    signals_enabled = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    # Minimum plan required to chart / watchlist this symbol. Default "free" =
    # available to everyone (all existing symbols). Set to "pro" to make a symbol
    # Pro-only (e.g. XAU-USD gold); enforced server-side in the candles endpoint
    # and watchlist add, and shown locked in the picker (apps.accounts.plans
    # .plan_allows is the single check).
    min_plan = models.CharField(
        max_length=16, choices=MinPlan.choices, default=MinPlan.FREE
    )

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


class MarketContext(models.Model):
    """Hourly snapshot of a perp's positioning data — open interest and funding.

    WHY THIS EXISTS AS A RECORDER RATHER THAN A FEATURE

    Every signal filter in this project re-slices the same OHLCV data, and by
    2026-09 that seam was measurably exhausted: entry-price selection, a momentum
    veto and six threshold sweeps all came back flat or negative out-of-sample.
    Positioning data is the first genuinely NEW information available — it says who
    is in the trade and what they are paying to stay there, which price alone does
    not contain. The specific idea worth testing is that open interest RISING with
    price means new money is backing the move, while FALLING open interest means the
    move is short covering and tends to stall — exactly the distinction the trend
    book currently cannot make.

    But Hyperliquid only serves the CURRENT value: `metaAndAssetCtxs` is a snapshot,
    and there is no historical open-interest endpoint. So unlike every other idea
    here, it cannot be backtested against the past — the history has to be built
    going forward before it can be measured at all. Hence a recorder that ships
    months before the feature: the alternative is shipping an untested filter, which
    is precisely what has cost this product money.

    (Funding IS available historically via `fundingHistory`, so it can be tested
    without waiting. It is recorded here anyway so both series share one clock and
    one row, which makes joining them to candles trivial later.)

    Bucketed to the hour to match the 1h signal timeframe, with uniqueness on
    (symbol, bucket) so a re-run or an overlapping beat tick cannot double-write.
    Nothing reads this yet by design.
    """

    symbol = models.ForeignKey(Symbol, on_delete=models.CASCADE, related_name="contexts")
    # Start of the UTC hour this snapshot belongs to.
    bucket = models.DateTimeField(db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    open_interest = models.FloatField(null=True, blank=True)
    funding = models.FloatField(null=True, blank=True)
    mark_px = models.FloatField(null=True, blank=True)
    day_ntl_vlm = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["symbol", "bucket"], name="uniq_symbol_ctx_bucket")
        ]
        indexes = [models.Index(fields=["symbol", "-bucket"])]
        ordering = ["-bucket"]

    def __str__(self):
        return f"{self.symbol_id} @ {self.bucket:%Y-%m-%d %H}Z oi={self.open_interest}"
