"""Unit tests for the pure sizing helpers (no DB, no broker)."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.auto_trade.models import AutoTradeConfig
from apps.auto_trade.sizing import (
    SizingError,
    compute_qty,
    filled_tp_legs,
    split_take_profits,
)


def _cfg(**over):
    """A duck-typed config — compute_qty only reads sizing_mode + the rate fields,
    and references the Sizing enum on the instance, so a SimpleNamespace works."""
    base = dict(
        sizing_mode=AutoTradeConfig.Sizing.RISK_PCT,
        risk_pct=1.0,
        fixed_usd=100.0,
        pct_balance=5.0,
        Sizing=AutoTradeConfig.Sizing,
    )
    base.update(over)
    return SimpleNamespace(**base)


class ComputeQtyTests(SimpleTestCase):
    def test_risk_pct_sizes_to_lose_exactly_risk_amount_at_stop(self):
        # 1% of 10k = $100 risk; entry 100, stop 95 → $5 risk/unit → 20 units.
        qty = compute_qty(10_000, entry=100.0, stop_loss=95.0, config=_cfg(risk_pct=1.0))
        self.assertAlmostEqual(qty, 20.0)
        # The whole point: losing the stop distance × qty equals the risk budget.
        self.assertAlmostEqual(abs(100.0 - 95.0) * qty, 100.0)

    def test_risk_pct_wider_stop_gives_smaller_position(self):
        narrow = compute_qty(10_000, 100.0, 99.0, _cfg(risk_pct=1.0))
        wide = compute_qty(10_000, 100.0, 90.0, _cfg(risk_pct=1.0))
        self.assertGreater(narrow, wide)

    def test_fixed_usd_ignores_stop_distance(self):
        qty = compute_qty(10_000, entry=50.0, stop_loss=10.0,
                          config=_cfg(sizing_mode=AutoTradeConfig.Sizing.FIXED_USD, fixed_usd=200.0))
        self.assertAlmostEqual(qty, 4.0)  # 200 / 50

    def test_pct_balance_sizes_by_notional(self):
        qty = compute_qty(10_000, entry=100.0, stop_loss=1.0,
                          config=_cfg(sizing_mode=AutoTradeConfig.Sizing.PCT_BALANCE, pct_balance=5.0))
        self.assertAlmostEqual(qty, 5.0)  # 5% of 10k = $500 notional / 100

    def test_zero_equity_raises(self):
        with self.assertRaises(SizingError):
            compute_qty(0, 100.0, 95.0, _cfg())

    def test_entry_equals_stop_raises(self):
        with self.assertRaises(SizingError):
            compute_qty(10_000, 100.0, 100.0, _cfg())

    def test_invalid_entry_raises(self):
        with self.assertRaises(SizingError):
            compute_qty(10_000, 0.0, -5.0, _cfg())


class SplitTakeProfitsTests(SimpleTestCase):
    def test_even_split_when_no_distribution(self):
        legs = split_take_profits(100.0, [1, 2, 3, 4], distribution=[])
        self.assertEqual([q for _, q in legs], [25.0, 25.0, 25.0, 25.0])

    def test_custom_distribution(self):
        legs = split_take_profits(100.0, [1, 2, 3, 4], distribution=[40, 30, 20, 10])
        self.assertEqual([q for _, q in legs], [40.0, 30.0, 20.0, 10.0])
        self.assertEqual([p for p, _ in legs], [1, 2, 3, 4])

    def test_zero_share_legs_are_dropped(self):
        legs = split_take_profits(100.0, [1, 2, 3, 4], distribution=[50, 50, 0, 0])
        self.assertEqual(len(legs), 2)

    def test_mismatched_length_falls_back_to_even(self):
        legs = split_take_profits(100.0, [1, 2, 3, 4], distribution=[50, 50])
        self.assertEqual(len(legs), 4)


class FilledTpLegsTests(SimpleTestCase):
    DIST = [25, 25, 25, 25]

    def test_even_distribution_boundaries(self):
        cases = {0.0: 0, 0.24: 0, 0.25: 1, 0.49: 1, 0.5: 2, 0.75: 3, 1.0: 4}
        for frac, expected in cases.items():
            self.assertEqual(filled_tp_legs(frac, self.DIST), expected, msg=f"frac={frac}")

    def test_exact_fill_not_missed_by_rounding(self):
        # A hair under an exact boundary still counts (epsilon tolerance).
        self.assertEqual(filled_tp_legs(0.2499999, self.DIST), 1)

    def test_uneven_distribution(self):
        dist = [50, 30, 20, 0]
        self.assertEqual(filled_tp_legs(0.49, dist), 0)
        self.assertEqual(filled_tp_legs(0.5, dist), 1)
        self.assertEqual(filled_tp_legs(0.8, dist), 2)
        self.assertEqual(filled_tp_legs(1.0, dist), 3)

    def test_empty_distribution_is_zero(self):
        self.assertEqual(filled_tp_legs(1.0, []), 0)
