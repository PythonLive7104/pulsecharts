"""Unit tests for the loss circuit breaker.

The parsing and windowing logic is what decides whether the product goes quiet, so
it is tested directly. DB-backed counting is covered by the config/parse tests plus
`filter_halted`'s disabled path, and TradeDedupTests covers the trade-vs-row
counting that decides how much evidence the breaker actually needs.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.market_data.models import Symbol
from apps.signals import breaker
from apps.signals.levels import compute_levels
from apps.signals.models import Signal, SignalService


def _sig(asset_class="crypto"):
    return SimpleNamespace(symbol=SimpleNamespace(asset_class=asset_class))


class ConfigTests(SimpleTestCase):
    @override_settings(SIGNAL_LOSS_BREAKER="")
    def test_empty_disables(self):
        self.assertIsNone(breaker._config())

    @override_settings(SIGNAL_LOSS_BREAKER="6/4/6")
    def test_parses(self):
        self.assertEqual(breaker._config(), (6, 4.0, 6.0))

    @override_settings(SIGNAL_LOSS_BREAKER="6/4")
    def test_wrong_arity_fails_open(self):
        # Fails OPEN, not closed: a config typo must never silence the whole product.
        self.assertIsNone(breaker._config())

    @override_settings(SIGNAL_LOSS_BREAKER="six/4/6")
    def test_non_numeric_fails_open(self):
        self.assertIsNone(breaker._config())

    @override_settings(SIGNAL_LOSS_BREAKER="0/4/6")
    def test_zero_losses_rejected(self):
        # 0 would halt delivery permanently the moment the breaker is switched on.
        self.assertIsNone(breaker._config())

    @override_settings(SIGNAL_LOSS_BREAKER="6/0/6")
    def test_zero_window_rejected(self):
        self.assertIsNone(breaker._config())


class FilterTests(SimpleTestCase):
    @override_settings(SIGNAL_LOSS_BREAKER="")
    def test_disabled_is_passthrough(self):
        reps = [_sig(), _sig("forex")]
        self.assertEqual(breaker.filter_halted(reps, timezone.now()), reps)


class StateTests(SimpleTestCase):
    @override_settings(SIGNAL_LOSS_BREAKER="")
    def test_state_disabled(self):
        st = breaker.breaker_state("crypto", timezone.now())
        self.assertFalse(st["halted"])
        self.assertEqual(st["threshold"], 0)


class TradeDedupTests(TestCase):
    """The breaker counts TRADES, not Signal rows.

    The scan writes one row per strategy, so a setup several strategies agree on
    stops out as several rows at the same instant while the user was delivered a
    single card. Counting rows let one bad trade spend three of a six-loss budget —
    the breaker fired on a third of the evidence it was tuned for, and lowering
    SIGNAL_CONFLUENCE_MIN made it worse by putting more rows behind each card. It is
    the same double-count stats.py dedupes for the published win rate.
    """

    def setUp(self):
        cache.clear()
        self.symbol = Symbol.objects.create(
            ticker="BTC-USD", hl_coin="BTC", asset_class="crypto", display_name="BTC")
        self.services = [
            SignalService.objects.create(name=n, slug=s, strategy_type="trend",
                                         is_active=True)
            for n, s in (("Momentum Crossover", "momentum-crossover"),
                         ("Bollinger Breakout", "bollinger-breakout"),
                         ("Volatility Breakout", "volatility-breakout"))
        ]

    def _stopped(self, service, entry, when, direction="BUY"):
        levels = compute_levels(direction, entry, atr=entry * 0.02,
                                swing_high=entry * 2, swing_low=entry / 2,
                                atr_stop_mult=1.0, max_atr_mult=1.0)
        return Signal.objects.create(
            symbol=self.symbol, service=service, timeframe="1h", direction=direction,
            confidence_pct=80, entry_price=entry, outcome=Signal.Outcome.SL,
            best_tp=0, generated_at=when, resolved_at=when, **levels)

    @override_settings(SIGNAL_LOSS_BREAKER="6/4/6")
    def test_one_setup_across_three_strategies_counts_once(self):
        now = timezone.now()
        for svc in self.services:
            self._stopped(svc, 100.0, now - timedelta(minutes=30))
        state = breaker.breaker_state("crypto", now)
        self.assertEqual(state["losses"], 1, "three rows, one trade")
        self.assertFalse(state["halted"])

    @override_settings(SIGNAL_LOSS_BREAKER="3/4/6")
    def test_distinct_setups_still_count_separately(self):
        now = timezone.now()
        for i, entry in enumerate((100.0, 200.0, 300.0)):
            self._stopped(self.services[0], entry, now - timedelta(minutes=10 * i + 5))
        state = breaker.breaker_state("crypto", now)
        self.assertEqual(state["losses"], 3)
        self.assertTrue(state["halted"], "three real losses must still trip a 3-loss rule")

    @override_settings(SIGNAL_LOSS_BREAKER="2/4/6")
    def test_row_duplication_cannot_trip_the_breaker_alone(self):
        # The regression this fixes: one losing trade, three strategies, a 2-loss
        # threshold. Counting rows would halt the whole feed on a single stop-out.
        now = timezone.now()
        for svc in self.services:
            self._stopped(svc, 100.0, now - timedelta(minutes=5))
        self.assertFalse(breaker.breaker_state("crypto", now)["halted"])
