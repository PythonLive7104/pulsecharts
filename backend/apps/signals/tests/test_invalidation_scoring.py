"""An invalidated trade is scored at the price it closed, not at a flat 0R.

Scoring a trend-flip close as exactly 0R was an assumption, never a fill. An EMA
stack breaking on a BUY means price has usually come DOWN off the entry, so the real
exit is a small loss — the assumption is OPTIMISTIC, and it ran in the direction that
flattered the published record.

It was not a rounding error. Measured over five coins and 7.7 years, scoring
invalidations flat turned a -0.01R strategy into +0.05R and a +2% capital curve into
+1005%, because the exit fired on ~95% of that strategy's trades.

The tests below pin both halves: that a recorded result is used, and that a row
without one still falls back to the table rather than guessing.
"""

from django.test import TestCase
from django.utils import timezone

from apps.market_data.models import Symbol
from apps.signals import tasks
from apps.signals.levels import compute_levels
from apps.signals.models import Signal, SignalService
from apps.signals.stats import outcome_r


class OutcomeRTests(TestCase):
    def test_recorded_result_wins_over_the_table(self):
        self.assertAlmostEqual(outcome_r("tp1", "INVALID", -0.42), -0.42)

    def test_missing_result_falls_back_to_flat(self):
        # Rows invalidated before the field existed: their exit price was never
        # recorded, and inventing one would be worse than the known-conservative 0.
        self.assertEqual(outcome_r("tp1", "INVALID", None), 0.0)
        self.assertEqual(outcome_r("tp1", "INVALID"), 0.0)

    def test_targets_and_stops_are_unaffected(self):
        self.assertEqual(outcome_r("tp1", "TP1"), 1.0)
        self.assertEqual(outcome_r("scaleout", "TP3"), 1.75)
        self.assertEqual(outcome_r("tp1", "SL"), -1.0)

    def test_a_zero_result_is_honoured_not_treated_as_missing(self):
        # 0.0 is a real outcome (closed exactly at entry) and must not be confused
        # with "nothing recorded" — the classic falsy-vs-None bug.
        self.assertEqual(outcome_r("tp1", "INVALID", 0.0), 0.0)
        self.assertAlmostEqual(outcome_r("tp1", "INVALID", -0.001), -0.001)


class InvalidationRecordsItsExitTests(TestCase):
    """`_invalidate_trend_breaks` must store what the trade actually made."""

    def setUp(self):
        self.symbol = Symbol.objects.create(
            ticker="BTC-USD", hl_coin="BTC", asset_class="crypto", display_name="BTC")
        self.service = SignalService.objects.create(
            name="Momentum Crossover", slug="momentum-crossover",
            strategy_type="momentum", is_active=True)

    def _signal(self, direction, entry, stop):
        """A Signal with a real, internally consistent level set.

        Built through ``compute_levels`` rather than hand-stubbed so the row carries
        the same geometry a live signal would: pinning the ATR band to exactly the
        risk distance puts the stop on `stop` whatever the swings say.
        """
        risk = abs(entry - stop)
        levels = compute_levels(
            direction, entry, atr=risk or 1.0,
            swing_high=entry * 2, swing_low=entry / 2,
            atr_stop_mult=1.0, max_atr_mult=1.0,
        ) if risk else None
        if levels is None:  # degenerate stop — the case one test needs
            levels = {"stop_loss": stop, "tp1": entry, "tp2": entry, "tp3": entry,
                      "tp4": None, "risk_pct": 0.0,
                      "reward_tp1_pct": 0.0, "reward_tp2_pct": 0.0,
                      "reward_tp3_pct": 0.0, "reward_tp4_pct": None,
                      "risk_reward_tp1": 0.0, "risk_reward_tp2": 0.0,
                      "risk_reward_tp3": 0.0, "risk_reward_tp4": None,
                      "dollar_risk": 0.0, "dollar_tp1": 0.0, "dollar_tp2": 0.0,
                      "dollar_tp3": 0.0, "dollar_tp4": None}
        return Signal.objects.create(
            symbol=self.symbol, service=self.service, timeframe="1h",
            direction=direction, confidence_pct=80, entry_price=entry,
            outcome=Signal.Outcome.PENDING, best_tp=0,
            generated_at=timezone.now(), **levels,
        )

    def test_buy_closing_below_entry_books_a_loss(self):
        # entry 100, stop 90 => 1R = 10. Broken stack with price at 96 is -0.4R.
        sig = self._signal("BUY", 100.0, 90.0)
        broken = {"ema9": 1.0, "ema21": 2.0, "ema200": 3.0, "close": 96.0}
        self.assertEqual(
            tasks._invalidate_trend_breaks(self.symbol, "1h", broken, timezone.now()), 1)
        sig.refresh_from_db()
        self.assertEqual(sig.outcome, Signal.Outcome.INVALIDATED)
        self.assertAlmostEqual(sig.exit_price, 96.0)
        self.assertAlmostEqual(sig.realized_r, -0.4, places=6)

    def test_sell_closing_above_entry_books_a_loss(self):
        # entry 100, stop 110 => 1R = 10. Price at 104 is -0.4R on a SELL.
        sig = self._signal("SELL", 100.0, 110.0)
        broken = {"ema9": 3.0, "ema21": 2.0, "ema200": 1.0, "close": 104.0}
        tasks._invalidate_trend_breaks(self.symbol, "1h", broken, timezone.now())
        sig.refresh_from_db()
        self.assertAlmostEqual(sig.realized_r, -0.4, places=6)

    def test_an_invalidation_in_profit_is_booked_as_profit(self):
        # Not every trend flip is a loser; the fix must not simply bias downward.
        sig = self._signal("BUY", 100.0, 90.0)
        broken = {"ema9": 1.0, "ema21": 2.0, "ema200": 3.0, "close": 103.0}
        tasks._invalidate_trend_breaks(self.symbol, "1h", broken, timezone.now())
        sig.refresh_from_db()
        self.assertAlmostEqual(sig.realized_r, 0.3, places=6)

    def test_an_intact_stack_closes_nothing(self):
        sig = self._signal("BUY", 100.0, 90.0)
        intact = {"ema9": 3.0, "ema21": 2.0, "ema200": 1.0, "close": 105.0}
        self.assertEqual(
            tasks._invalidate_trend_breaks(self.symbol, "1h", intact, timezone.now()), 0)
        sig.refresh_from_db()
        self.assertEqual(sig.outcome, Signal.Outcome.PENDING)
        self.assertIsNone(sig.realized_r)

    def test_a_degenerate_stop_is_skipped_not_crashed(self):
        # entry == stop would divide by zero in the UPDATE and roll back the whole
        # batch, leaving every genuinely-broken trade open.
        sig = self._signal("BUY", 100.0, 100.0)
        broken = {"ema9": 1.0, "ema21": 2.0, "ema200": 3.0, "close": 96.0}
        self.assertEqual(
            tasks._invalidate_trend_breaks(self.symbol, "1h", broken, timezone.now()), 0)
        sig.refresh_from_db()
        self.assertEqual(sig.outcome, Signal.Outcome.PENDING)

    def test_missing_close_records_nothing(self):
        # Without a price there is no honest exit to record, so the call stays open
        # rather than being closed at a number nobody measured.
        sig = self._signal("BUY", 100.0, 90.0)
        broken = {"ema9": 1.0, "ema21": 2.0, "ema200": 3.0, "close": None}
        self.assertEqual(
            tasks._invalidate_trend_breaks(self.symbol, "1h", broken, timezone.now()), 0)
        sig.refresh_from_db()
        self.assertEqual(sig.outcome, Signal.Outcome.PENDING)

    def test_a_banked_target_is_never_invalidated(self):
        # Its stop is at breakeven and the partial is real money; closing it flat
        # would erase a booked gain.
        sig = self._signal("BUY", 100.0, 90.0)
        sig.best_tp = 1
        sig.save(update_fields=["best_tp"])
        broken = {"ema9": 1.0, "ema21": 2.0, "ema200": 3.0, "close": 96.0}
        self.assertEqual(
            tasks._invalidate_trend_breaks(self.symbol, "1h", broken, timezone.now()), 0)
        sig.refresh_from_db()
        self.assertEqual(sig.outcome, Signal.Outcome.PENDING)
