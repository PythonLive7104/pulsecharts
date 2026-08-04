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
        "is_active": False,  # disabled: negative expectancy in 3 straight backtests (~-0.5R), gated AND native
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
        "is_active": False,  # disabled: weakest of the roster — negative at TP1 (-0.06R)
                             # and only +0.04R even under scale-out (n=332); other breakout
                             # strategies (volatility, donchian) were cut for the same reason.
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
        "is_active": False,  # disabled 2026-07-21: net loser in backtest (48% win, -0.09R scale) —
                             # the weakest voter dragging the confluence feed. Reversible.
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
    # --- mean reversion (kind: reversion — pregate.STRATEGY_KIND) -------------
    # Seeded INACTIVE. These are the first non-trend strategies in the roster: the
    # six active ones are six expressions of a single trend-following edge, so they
    # all lose together in a range. These fade extremes instead, and are gated
    # differently (ADX ceiling not floor, no EMA-stack/structure/overextension
    # requirement, tighter stops) — see pregate.kind_of. Activate only if
    # `backtest --include-inactive` shows a real edge.
    {
        "name": "Bollinger Band Fade",
        "slug": "bb-fade",
        "strategy_type": "reversion",
        "description": "Fades a close outside a Bollinger band when RSI is at an extreme.",
        "strategy_focus": (
            "In a range (low ADX), a close beyond the outer Bollinger band with RSI "
            "stretched to an extreme tends to snap back toward the middle band."
        ),
        # ACTIVE 2026-08-03. 55.2% / +0.26R over n=440 (20 syms, 1200 candles, live
        # gates) — the largest sample and the highest expectancy of any strategy in
        # the roster, against +0.02R for the best ACTIVE trend strategy. Fires at
        # ADX <= 20, a regime the trend book is gated out of, so it adds trades rather
        # than duplicating them. NOTE: is_active is in `defaults`, so flipping the row
        # in the admin without changing this line gets reverted by the next seed run.
        "is_active": True,
    },
    {
        "name": "RSI Exhaustion Reversal",
        "slug": "rsi-exhaustion",
        "strategy_type": "reversion",
        "description": "Fades a move when RSI and Stochastic are both at an extreme.",
        "strategy_focus": (
            "Two independent oscillators at the same extreme (RSI <=25 with Stochastic "
            "<=20, or RSI >=75 with Stochastic >=80) while price sits beyond the mean "
            "suggests the move is spent rather than continuing."
        ),
        "is_active": False,
    },
    {
        "name": "VWAP Stretch Reversion",
        "slug": "vwap-stretch",
        "strategy_type": "reversion",
        "description": "Fades price stretched more than 2xATR away from VWAP.",
        "strategy_focus": (
            "Price a long way from VWAP in ATR terms tends to revert toward it when no "
            "strong trend is driving the move. Distance is measured in ATR so it "
            "adapts to each symbol's volatility."
        ),
        # ACTIVE 2026-08-03. Reverses the earlier call: it read -0.03R at n=36, but
        # 55.6% / +0.27R at n=180 on the 20-symbol run. The small sample was the
        # wrong one.
        "is_active": True,
    },

    {
        "name": "Sweep Reversal",
        "slug": "sweep-reversal",
        "strategy_type": "reversion",
        "description": "Fades a failed breakout — price sweeps a swing level and closes back inside.",
        "strategy_focus": (
            "Price pokes through a recent swing high or low, then closes back inside "
            "the range. The breakout failed and the traders who chased it are trapped, "
            "which tends to push price the other way."
        ),
        # INACTIVE pending a backtest. Different TRIGGER from the other two fades:
        # they fire on distance from a mean (band, VWAP), this one on rejection at a
        # level — so it should fire on bars they miss rather than duplicating them.
        "is_active": False,
    },

    {
        "name": "RSI(2) Reversion",
        "slug": "rsi2-reversion",
        "strategy_type": "reversion",
        "description": "Larry Connors' RSI(2) — fades a 2-period RSI extreme in the direction of the 200 EMA.",
        "strategy_focus": (
            "A 2-period RSI below 5 while price holds above the 200 EMA (or above 95 "
            "while below it) marks a short-term washout inside a longer trend — the "
            "most documented short-term mean-reversion setup there is."
        ),
        # ACTIVE 2026-08-04. 54.1% / +0.19R over n=305 (20 syms, 1200 candles, live
        # gates) — best of the four reversion candidates tested that day, and its
        # trigger (2-period RSI + 200-EMA side) is independent of the "distance from
        # a mean" logic the other two fades share, so it adds setups rather than
        # duplicating them. The three that stayed off: Sweep Reversal (+0.13R) and
        # Down-Streak (+0.10R) fire hugely often but at half the quality; Volume
        # Climax (+0.03R) was the weakest of the group.
        "is_active": True,
    },
    {
        "name": "Down-Streak Reversion",
        "slug": "streak-reversion",
        "strategy_type": "reversion",
        "description": "Fades three or more consecutive closes in the same direction.",
        "strategy_focus": (
            "Three consecutive lower closes (or higher) is a stretched short-term move. "
            "It uses no oscillator at all, so it fires on bars the band and VWAP "
            "strategies never see."
        ),
        "is_active": False,
    },
    {
        "name": "Volume Climax Reversal",
        "slug": "volume-climax",
        "strategy_type": "reversion",
        "description": "Fades a capitulation bar — huge volume, wide range, closing back at the other end.",
        "strategy_focus": (
            "A bar on 2.5x average volume with a range over 1.5x ATR that closes back "
            "in the top (or bottom) third of itself is exhaustion: the move ran out of "
            "participants and the other side took control before the close."
        ),
        "is_active": False,
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
        # Scoped to built-ins (owner=None): SERVICES only describes built-ins, so a
        # user's custom strategy is never "stale" — including them here deactivated
        # (or deleted, when brand new) somebody's strategy on every deploy.
        keep = {s["slug"] for s in SERVICES}
        deleted = deactivated = 0
        for svc in SignalService.objects.filter(owner__isnull=True).exclude(slug__in=keep):
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
