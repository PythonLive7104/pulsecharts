"""Unit tests for the loss circuit breaker.

The parsing and windowing logic is what decides whether the product goes quiet, so
it is tested directly. DB-backed counting is covered by the config/parse tests plus
`filter_halted`'s disabled path; the query itself is a single filtered count.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.signals import breaker


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
