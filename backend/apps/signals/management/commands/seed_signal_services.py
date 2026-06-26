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
            "in the direction of the EMA 200 trend."
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
        "description": "Trade with the EMA 200 trend, fast EMAs aligned and RSI confirming.",
        "strategy_focus": (
            "Trend-following setup: only go long when price is above the EMA 200 with "
            "EMA 9 above EMA 21 and RSI above 50; only go short when price is below the "
            "EMA 200 with EMA 9 below EMA 21 and RSI below 50. Strongest when MACD "
            "histogram agrees with the trend direction. Avoid counter-trend calls."
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
        "name": "Trend Pullback",
        "slug": "trend-pullback",
        "strategy_type": "trend",
        "description": "Buy the dip / sell the rally inside an established EMA 200 trend.",
        "strategy_focus": (
            "Trend-continuation pullback entry: in an uptrend (price above EMA 200, EMA 9 "
            "above EMA 21) go long when RSI has cooled into the 40–50 pullback zone and is "
            "turning back up; in a downtrend go short when RSI has bounced into the 50–60 "
            "zone and is rolling over. The idea is to join the trend on a dip, not to chase "
            "an extended move."
        ),
    },
    {
        "name": "EMA Ribbon",
        "slug": "ema-ribbon",  # active: +0.03R in backtest, on par with active peers
        "strategy_type": "trend",
        "description": "Fully-stacked EMA 9/21/200 alignment with price riding the ribbon.",
        "strategy_focus": (
            "Strong-trend continuation: go long only when the EMAs are fully stacked up "
            "(EMA 9 above EMA 21 above EMA 200) and price is holding above the ribbon; go "
            "short when fully stacked down with price below it. The cleaner and wider the "
            "stack, the stronger the trend — skip when the EMAs are tangled with no clear order."
        ),
    },
    {
        "name": "Donchian Turtle Trend",
        "slug": "donchian-trend",
        "is_active": False,  # disabled: negative expectancy in backtest (-0.10R, n=29)
        "strategy_type": "trend",
        "description": "Turtle-style channel breakout aligned with the EMA 200 trend.",
        "strategy_focus": (
            "Classic Turtle trend-following: go long when price breaks above the recent "
            "swing-high channel while above the EMA 200; go short when it breaks the swing-low "
            "channel while below the EMA 200. Trade the breakout only in the direction of the "
            "major trend — ignore counter-trend channel pokes."
        ),
    },
    {
        "name": "ADX Directional Trend",
        "slug": "adx-trend",  # active: +0.15R in backtest, best of the roster
        "strategy_type": "trend",
        "description": "Trade only confirmed strong trends (ADX > 25), direction from the EMAs.",
        "strategy_focus": (
            "Trend-strength filtered entry: only act when ADX is above 25 (a genuinely strong "
            "trend, not chop). Then go long when price is above the EMA 200 with EMA 9 above "
            "EMA 21, or short when below the EMA 200 with EMA 9 below EMA 21. A rising ADX "
            "strengthens the case; a falling ADX warns the trend is fading."
        ),
    },
]


class Command(BaseCommand):
    help = "Seed/reconcile the signal services to the canonical list above."

    def handle(self, *args, **options):
        created = 0
        for s in SERVICES:
            _, was_created = SignalService.objects.update_or_create(slug=s["slug"], defaults=s)
            created += int(was_created)

        # Reconcile: drop services no longer in the canonical list so a removed
        # strategy doesn't linger after deploy (update_or_create never deletes).
        # Preserve history — a stale service WITH signals is deactivated, not
        # cascade-deleted; only a truly orphaned one (no signals) is hard-deleted.
        keep = {s["slug"] for s in SERVICES}
        deleted = deactivated = 0
        for svc in SignalService.objects.exclude(slug__in=keep):
            if svc.signals.exists():
                if svc.is_active:
                    svc.is_active = False
                    svc.save(update_fields=["is_active"])
                deactivated += 1
                self.stdout.write(self.style.WARNING(
                    f"  '{svc.slug}' removed from list but has signal history — deactivated, kept."
                ))
            else:
                svc.delete()
                deleted += 1
                self.stdout.write(f"  removed stale service '{svc.slug}'.")

        tail = "".join([
            f", {deleted} stale removed" if deleted else "",
            f", {deactivated} stale deactivated" if deactivated else "",
        ])
        self.stdout.write(
            self.style.SUCCESS(f"Seeded {len(SERVICES)} signal services ({created} new{tail}).")
        )
