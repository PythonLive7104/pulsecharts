"""Seed the starting set of signal services (Section 13.2)."""

from django.core.management.base import BaseCommand

from apps.signals.models import SignalService

SERVICES = [
    {
        "name": "Momentum Crossover",
        "slug": "momentum-crossover",
        "strategy_type": "momentum",
        "description": "EMA crossover confirmed by RSI moving in the same direction.",
        "strategy_focus": (
            "Look for EMA 9 crossing above/below EMA 21 confirmed by RSI direction "
            "and MACD histogram expanding in the same direction."
        ),
    },
    {
        "name": "MACD Trend Following",
        "slug": "macd-trend-following",
        "strategy_type": "trend",
        "description": "Signal-line crossovers combined with histogram strength.",
        "strategy_focus": (
            "Look for MACD line crossing its signal line with an expanding histogram, "
            "in the direction of the EMA 50 trend."
        ),
    },
    {
        "name": "Bollinger Band Mean Reversion",
        "slug": "bollinger-mean-reversion",
        "is_active": False,  # disabled: negative expectancy in backtest (mean-reversion)
        "strategy_type": "mean_reversion",
        "description": "Price touching/exceeding a band combined with RSI at an extreme.",
        "strategy_focus": (
            "Look for price touching or exceeding the upper/lower Bollinger Band while "
            "RSI is at an extreme (overbought/oversold), anticipating reversion to the mean."
        ),
    },
    {
        "name": "Volatility Breakout",
        "slug": "volatility-breakout",
        "strategy_type": "breakout",
        "description": "ATR expansion combined with a break of a recent price range.",
        "strategy_focus": (
            "Look for ATR expansion alongside a break of the recent swing high/low range, "
            "confirmed by above-average volume."
        ),
    },
    {
        "name": "Trend Rider (EMA + RSI)",
        "slug": "trend-rider",
        "strategy_type": "trend",
        "description": "Trade with the EMA 50 trend, fast EMAs aligned and RSI confirming.",
        "strategy_focus": (
            "Trend-following setup: only go long when price is above the EMA 50 with "
            "EMA 9 above EMA 21 and RSI above 50; only go short when price is below the "
            "EMA 50 with EMA 9 below EMA 21 and RSI below 50. Strongest when MACD "
            "histogram agrees with the trend direction. Avoid counter-trend calls."
        ),
    },
    {
        "name": "Stochastic Reversal",
        "slug": "stochastic-reversal",
        "is_active": False,  # disabled: negative expectancy in backtest (mean-reversion)
        "strategy_type": "mean_reversion",
        "description": "Stochastic %K/%D crossover out of overbought/oversold zones.",
        "strategy_focus": (
            "Reversal setup for range-bound conditions: go long when Stochastic %K is in "
            "the oversold zone (below 25) and crossing up over %D; go short when %K is in "
            "the overbought zone (above 75) and crossing down below %D. Prefer setups where "
            "RSI is also leaving an extreme and price is near a swing level. Skip when a "
            "strong trend (price far from VWAP/EMA 50) would override a reversion."
        ),
    },
    {
        "name": "VWAP Trend",
        "slug": "vwap-trend",
        "strategy_type": "trend",
        "description": "Price holding above/below session VWAP with momentum agreeing.",
        "strategy_focus": (
            "Intraday trend setup using VWAP as the line in the sand: go long when price is "
            "holding above VWAP with RSI above 50; go short when price is below VWAP with "
            "RSI below 50. Treat VWAP as dynamic support/resistance — the cleanest signals "
            "are a reclaim/rejection of VWAP confirmed by the EMA 9/21 slope."
        ),
    },
    {
        "name": "Bollinger Breakout",
        "slug": "bollinger-breakout",
        "strategy_type": "breakout",
        "description": "Close beyond a Bollinger Band on expanding volume — breakout continuation.",
        "strategy_focus": (
            "Breakout continuation (the opposite stance to mean reversion): go long when "
            "price closes at or above the upper Bollinger Band with RSI above 55 and "
            "above-average volume; go short when price closes at or below the lower band "
            "with RSI below 45 and rising volume. The expanding bands and volume should "
            "confirm real momentum, not a single-candle spike."
        ),
    },
    {
        "name": "VWAP Reversion",
        "slug": "vwap-reversion",
        "is_active": False,  # disabled: negative expectancy in backtest (mean-reversion)
        "strategy_type": "mean_reversion",
        "description": "Price stretched far from VWAP with RSI extreme — snap back to the mean.",
        "strategy_focus": (
            "Mean reversion to VWAP: go long when price is stretched at least one ATR below "
            "VWAP with RSI oversold (around 40 or lower); go short when price is stretched at "
            "least one ATR above VWAP with RSI overbought (around 60 or higher). Expect a "
            "reversion toward VWAP. Skip if a strong trend is likely to keep price extended."
        ),
    },
    {
        "name": "Trend Pullback",
        "slug": "trend-pullback",
        "strategy_type": "trend",
        "description": "Buy the dip / sell the rally inside an established EMA 50 trend.",
        "strategy_focus": (
            "Trend-continuation pullback entry: in an uptrend (price above EMA 50, EMA 9 "
            "above EMA 21) go long when RSI has cooled into the 40–50 pullback zone and is "
            "turning back up; in a downtrend go short when RSI has bounced into the 50–60 "
            "zone and is rolling over. The idea is to join the trend on a dip, not to chase "
            "an extended move."
        ),
    },
]


class Command(BaseCommand):
    help = "Seed the starter signal services."

    def handle(self, *args, **options):
        created = 0
        for s in SERVICES:
            _, was_created = SignalService.objects.update_or_create(slug=s["slug"], defaults=s)
            created += int(was_created)
        self.stdout.write(
            self.style.SUCCESS(f"Seeded {len(SERVICES)} signal services ({created} new).")
        )
