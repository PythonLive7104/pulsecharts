"""Unit tests for the pure position sizer (apps.execution.sizing).

No Django, no Bybit — plan_order is dependency-free, so these are fast and exhaustive
around the two constraints it balances: clear the exchange minimums, and keep
liquidation beyond the stop.
"""

from django.test import SimpleTestCase

from apps.execution.sizing import _floor_step, _liq_price, plan_order, split_scaleout


class FloorStepTests(SimpleTestCase):
    def test_rounds_down_to_step(self):
        self.assertEqual(_floor_step(1.257, 0.01), 1.25)
        self.assertEqual(_floor_step(1.0, 0.001), 1.0)

    def test_zero_step_is_passthrough(self):
        self.assertEqual(_floor_step(1.234, 0), 1.234)

    def test_binary_drift_at_edge(self):
        # 0.3 / 0.1 == 2.9999999 in float; the epsilon must let it land on 3 steps.
        self.assertEqual(_floor_step(0.3, 0.1), 0.3)


class PlanOrderTests(SimpleTestCase):
    # A generous, liquid symbol: tiny min qty, cheap price, high max leverage.
    BASE = dict(
        min_order_qty=0.001, qty_step=0.001, min_notional=5.0, max_leverage=50,
    )

    def test_happy_path_picks_lowest_safe_leverage(self):
        # $1 margin, entry 100, stop 97 (3% stop). need_notional = max(5, 0.001*100)=5,
        # so lev_lo = ceil(5/1) = 5. That clears both minimums.
        plan = plan_order(1.0, 100.0, 97.0, **self.BASE)
        self.assertTrue(plan.ok, plan.reason)
        self.assertGreaterEqual(plan.leverage, 5)
        self.assertGreaterEqual(plan.notional, 5.0)
        self.assertGreaterEqual(plan.qty, self.BASE["min_order_qty"])
        # Liquidation must sit BELOW the stop for a long (further from entry).
        self.assertLess(plan.liq_price, 97.0)

    def test_liquidation_stays_beyond_stop(self):
        plan = plan_order(1.0, 100.0, 95.0, **self.BASE)
        self.assertTrue(plan.ok, plan.reason)
        is_long = True
        # Recompute liq at the chosen leverage and assert it's beyond the stop + buffer.
        liq = _liq_price(100.0, plan.leverage, 0.005, is_long)
        self.assertLess(liq, 95.0)

    def test_short_side_liquidation_above_stop(self):
        # stop above entry ⇒ short. Liq must be above the stop.
        plan = plan_order(1.0, 100.0, 103.0, **self.BASE)
        self.assertTrue(plan.ok, plan.reason)
        self.assertGreater(plan.liq_price, 103.0)

    def test_stop_too_wide_to_place_safely(self):
        # A 40% stop needs such low leverage that the min notional can't be met at $1.
        # lev_safe = floor(1/(0.40+0.005+0.005)) = floor(2.43) = 2; need lev 5 for min
        # notional ⇒ lev_lo(5) > lev_hi(2) ⇒ skip.
        plan = plan_order(1.0, 100.0, 60.0, **self.BASE)
        self.assertFalse(plan.ok)
        self.assertIn("stop too wide", plan.reason)

    def test_balance_too_small_for_symbol_minimum(self):
        # A pricey min notional the margin can't reach even at max leverage.
        plan = plan_order(
            1.0, 100.0, 99.0,
            min_order_qty=1.0, qty_step=1.0, min_notional=200.0, max_leverage=10,
        )
        self.assertFalse(plan.ok)
        self.assertIn("balance too small", plan.reason)

    def test_invalid_inputs_rejected(self):
        self.assertFalse(plan_order(0, 100, 97, **self.BASE).ok)
        self.assertFalse(plan_order(1.0, 0, 97, **self.BASE).ok)

    def test_stop_equals_entry_rejected(self):
        plan = plan_order(1.0, 100.0, 100.0, **self.BASE)
        self.assertFalse(plan.ok)

    def test_qty_respects_step_rounding(self):
        # qty must be an exact multiple of qty_step (0.01 here).
        plan = plan_order(
            5.0, 33.0, 31.0,
            min_order_qty=0.01, qty_step=0.01, min_notional=5.0, max_leverage=25,
        )
        self.assertTrue(plan.ok, plan.reason)
        # qty / step is (near) integer.
        ratio = plan.qty / 0.01
        self.assertAlmostEqual(ratio, round(ratio), places=6)

    def test_margin_never_exceeds_allocation(self):
        # The margin the trade ties up should be ~ the allocation (qty rounded down),
        # never more — floor rounding must not overshoot the budget.
        plan = plan_order(2.0, 100.0, 96.0, **self.BASE)
        self.assertTrue(plan.ok, plan.reason)
        self.assertLessEqual(plan.margin, 2.0 + 1e-6)

    def test_max_leverage_cap_is_honored(self):
        # Even with a tight stop that would permit high leverage, never exceed the cap.
        plan = plan_order(
            1.0, 100.0, 99.5,  # 0.5% stop
            min_order_qty=0.001, qty_step=0.001, min_notional=5.0, max_leverage=8,
        )
        if plan.ok:
            self.assertLessEqual(plan.leverage, 8)


class SplitScaleoutTests(SimpleTestCase):
    FRACS = (0.5, 0.25, 0.25)

    def test_even_split_sums_to_qty(self):
        # qty 1.0, step 0.001 → 0.5 / 0.25 / 0.25, summing exactly to 1.0.
        tranches = split_scaleout(1.0, self.FRACS, qty_step=0.001, min_order_qty=0.001)
        self.assertEqual(tranches, [0.5, 0.25, 0.25])
        self.assertAlmostEqual(sum(tranches), 1.0, places=9)

    def test_remainder_goes_to_last_nonzero(self):
        # qty 0.999 step 0.1 → floor gives 0.4 / 0.2 / 0.2 = 0.8; remainder 0.1 to last.
        tranches = split_scaleout(0.999, self.FRACS, qty_step=0.1, min_order_qty=0.1)
        self.assertAlmostEqual(sum(tranches), 0.9, places=9)  # floored to step multiples
        self.assertGreater(tranches[2], tranches[1])  # last absorbed the remainder

    def test_sum_never_exceeds_qty(self):
        for qty in (0.37, 1.0, 2.5, 0.113):
            tranches = split_scaleout(qty, self.FRACS, qty_step=0.001, min_order_qty=0.001)
            self.assertLessEqual(sum(tranches), qty + 1e-9)

    def test_tiny_position_returns_all_zero(self):
        # qty == min_order_qty: no fraction can meet the minimum → all zero (fallback).
        tranches = split_scaleout(0.001, self.FRACS, qty_step=0.001, min_order_qty=0.001)
        self.assertEqual(tranches, [0.0, 0.0, 0.0])

    def test_sub_min_tranche_zeroed(self):
        # qty 0.003, step 0.001, min 0.002: 0.5→0.001 (<min→0), 0.25→0.0 each. All zero.
        tranches = split_scaleout(0.003, self.FRACS, qty_step=0.001, min_order_qty=0.002)
        self.assertTrue(all(t == 0.0 or t >= 0.002 for t in tranches))

    def test_zero_qty(self):
        self.assertEqual(
            split_scaleout(0.0, self.FRACS, qty_step=0.001, min_order_qty=0.001),
            [0.0, 0.0, 0.0],
        )
