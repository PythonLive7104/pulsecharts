"""Capital-curve engine — the arithmetic the public performance page rests on.

Every number here ends up in front of a paying user as a claim about money, so the
tests pin exact values rather than asserting "greater than zero": an off-by-one in
the drawdown walk or a sizing bug that quietly compounds unrealized P&L would still
produce a plausible-looking curve. No database — ``performance`` is deliberately
ORM-free so one implementation serves the backtest and the live ledger alike.
"""

from datetime import datetime, timezone
from unittest import mock

from django.test import SimpleTestCase

from apps.signals import performance
from apps.signals.performance import Trade

H = 3600


def ts(year, month=1, day=1):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class CapitalCurveTests(SimpleTestCase):
    def test_compounding_sizes_off_realized_equity(self):
        # +1R then -1R then +1R at 1% risk: the loss is sized off the larger balance,
        # so the round trip does NOT return to par.
        curve = performance.capital_curve(
            [Trade(0, H, 1.0), Trade(2 * H, 3 * H, -1.0), Trade(4 * H, 5 * H, 1.0)],
            10_000, 1.0, compounding=True)
        self.assertEqual([p["equity"] for p in curve["points"]],
                         [10_100.0, 9_999.0, 10_098.99])

    def test_fixed_sizing_ignores_equity(self):
        curve = performance.capital_curve(
            [Trade(0, H, 1.0), Trade(2 * H, 3 * H, -1.0), Trade(4 * H, 5 * H, 1.0)],
            10_000, 1.0, compounding=False)
        self.assertEqual([p["equity"] for p in curve["points"]],
                         [10_100.0, 10_000.0, 10_100.0])

    def test_open_positions_do_not_size_later_entries(self):
        # The winner is still open when the second trade is entered, so the second
        # trade sizes off 10_000, not off the unrealized 10_100. Counting unrealized
        # P&L would let one running winner inflate every position beside it.
        curve = performance.capital_curve(
            [Trade(0, 10 * H, 1.0), Trade(H, 2 * H, -1.0)], 10_000, 1.0)
        self.assertEqual([p["equity"] for p in curve["points"]], [9_900.0, 10_000.0])

    def test_a_close_settles_before_an_entry_on_the_same_bar(self):
        # Capital freed by a close is available to the trade entered at that instant,
        # which is how an account behaves — and it must not read as 2 concurrent.
        curve = performance.capital_curve(
            [Trade(0, H, 1.0), Trade(H, 2 * H, 1.0)], 10_000, 1.0)
        self.assertEqual(curve["max_concurrent"], 1)
        self.assertEqual(curve["points"][-1]["equity"], 10_201.0)

    def test_concurrency_reports_total_risk_not_per_trade_risk(self):
        curve = performance.capital_curve(
            [Trade(0, 10 * H, 1.0), Trade(H, 5 * H, -1.0), Trade(2 * H, 4 * H, -1.0)],
            10_000, 1.0)
        self.assertEqual(curve["max_concurrent"], 3)
        self.assertEqual(curve["peak_risk_pct"], 3.0)  # "1% risk" really ran 3%
        self.assertEqual(curve["exposure_pct"], 100.0)

    def test_exposure_counts_only_time_with_a_position_open(self):
        # Open 0-1h and 3-4h out of a 0-4h span.
        curve = performance.capital_curve(
            [Trade(0, H, 1.0), Trade(3 * H, 4 * H, 1.0)], 10_000, 1.0)
        self.assertEqual(curve["exposure_pct"], 50.0)

    def test_drawdown_and_time_underwater(self):
        curve = performance.capital_curve(
            [Trade(0, H, 1.0), Trade(H, 2 * H, -1.0), Trade(2 * H, 3 * H, -1.0),
             Trade(3 * H, 100 * H, 3.0)],
            10_000, 10.0, compounding=False)
        # Peak 11_000 at 1h, trough 9_000 at 3h: 2_000/11_000.
        self.assertAlmostEqual(curve["max_drawdown_pct"], 18.18, places=2)
        self.assertEqual(curve["max_drawdown_from"], H)
        self.assertEqual(curve["max_drawdown_at"], 3 * H)
        # Underwater is measured peak -> RECOVERY (1h to 100h), not peak -> trough:
        # the wait to get back to even is what a subscriber actually endures.
        self.assertEqual(curve["longest_underwater_days"], round(99 * H / 86400, 1))

    def test_a_zero_duration_trade_still_moves_the_curve(self):
        # Resolved in the bar it was issued, or a stored row with no resolved_at.
        # Its close must not settle before its own open, or the P&L would vanish from
        # the curve while still being counted in the metrics.
        curve = performance.capital_curve(
            [Trade(H, H, 1.0), Trade(2 * H, 3 * H, -1.0)], 10_000, 1.0)
        self.assertEqual([p["equity"] for p in curve["points"]], [10_100.0, 9_999.0])
        self.assertEqual(curve["max_concurrent"], 1)  # never went negative

    def test_a_curve_that_never_dips_is_not_underwater(self):
        # Equity at par for 100 days, then a winner: 0% drawdown, 0 days underwater.
        # Measuring peak->now unconditionally would report the whole span.
        curve = performance.capital_curve([Trade(0, 100 * H, 1.0)], 10_000, 1.0)
        self.assertEqual(curve["max_drawdown_pct"], 0.0)
        self.assertEqual(curve["longest_underwater_days"], 0.0)

    def test_an_unrecovered_drawdown_runs_to_the_end_of_the_sample(self):
        curve = performance.capital_curve(
            [Trade(0, H, 1.0), Trade(H, 2 * H, -1.0), Trade(2 * H, 50 * H, 0.0)],
            10_000, 1.0)
        self.assertGreater(curve["max_drawdown_pct"], 0)
        self.assertEqual(curve["longest_underwater_days"], round(49 * H / 86400, 1))

    def test_ruin_floors_equity_and_reports_itself(self):
        curve = performance.capital_curve(
            [Trade(0, H, -1.0), Trade(2 * H, 3 * H, 5.0)], 1_000, 100.0)
        self.assertTrue(curve["ruined"])
        self.assertEqual(curve["end_capital"], 0.0)   # nothing left to size the winner
        self.assertIsNone(curve["cagr_pct"])

    def test_cagr_needs_a_real_span(self):
        short = performance.capital_curve([Trade(0, H, 1.0)], 10_000, 1.0)
        self.assertIsNone(short["cagr_pct"])
        # One +1R trade at 100% risk doubles the account over exactly 2 years.
        long = performance.capital_curve(
            [Trade(ts(2020), ts(2022), 1.0)], 10_000, 100.0, compounding=False)
        self.assertEqual(long["end_capital"], 20_000.0)
        self.assertAlmostEqual(long["cagr_pct"], 41.4, places=0)

    def test_empty_and_invalid_inputs(self):
        empty = performance.capital_curve([], 10_000, 1.0)
        self.assertEqual(empty["end_capital"], 10_000)
        self.assertEqual(empty["points"], [])
        self.assertIsNone(empty["exposure_pct"])
        with self.assertRaises(ValueError):
            performance.capital_curve([], 10_000, 0)
        with self.assertRaises(ValueError):
            Trade(10, 5, 1.0)  # closed before it opened


class TradeMetricsTests(SimpleTestCase):
    def test_metrics_on_a_mixed_sample(self):
        m = performance.trade_metrics(
            [Trade(0, 1, 2.0), Trade(0, 1, -1.0), Trade(0, 1, -1.0), Trade(0, 1, 0.0)])
        self.assertEqual(m["profit_factor"], 1.0)      # 2.0 gross profit / 2.0 loss
        self.assertEqual(m["expectancy_r"], 0.0)
        self.assertEqual(m["payoff_ratio"], 2.0)
        self.assertEqual(m["max_loss_streak"], 2)
        self.assertEqual(m["max_r_drawdown"], 2.0)
        # A scratch is counted apart from both columns, and is not in the rate.
        self.assertEqual((m["profitable"], m["unprofitable"], m["flat"]), (1, 2, 1))
        self.assertEqual(m["profitable_rate"], 25.0)

    def test_a_flat_trade_breaks_a_streak_without_joining_one(self):
        m = performance.trade_metrics(
            [Trade(0, 1, -1.0), Trade(0, 1, 0.0), Trade(0, 1, -1.0)])
        self.assertEqual(m["max_loss_streak"], 1)

    def test_profit_factor_is_none_rather_than_infinite(self):
        self.assertIsNone(performance.trade_metrics([Trade(0, 1, 1.0)])["profit_factor"])

    def test_empty(self):
        m = performance.trade_metrics([])
        self.assertEqual(m["trades"], 0)
        self.assertIsNone(m["expectancy_r"])
        self.assertIsNone(m["profitable_rate"])


class PeriodReturnTests(SimpleTestCase):
    def test_yearly_returns_chain_off_the_previous_period(self):
        trades = [Trade(ts(2020), ts(2020, 2), 1.0),
                  Trade(ts(2021), ts(2021, 2), -1.0),
                  Trade(ts(2021, 3), ts(2021, 4), 1.0)]
        curve = performance.capital_curve(trades, 10_000, 10.0)
        rows = performance.period_returns(curve["points"], 10_000, "year")
        self.assertEqual([r["period"] for r in rows], ["2020", "2021"])
        self.assertEqual(rows[0]["return_pct"], 10.0)
        self.assertEqual(rows[1]["start_equity"], 11_000.0)  # continues, not restarts
        self.assertEqual(rows[1]["trades"], 2)

    def test_periods_without_a_closed_trade_are_omitted_not_zeroed(self):
        # A flat month and a month the strategy sat out are different facts.
        curve = performance.capital_curve(
            [Trade(ts(2020, 1), ts(2020, 1, 2), 1.0),
             Trade(ts(2020, 5), ts(2020, 5, 2), 1.0)], 10_000, 1.0)
        rows = performance.period_returns(curve["points"], 10_000, "month")
        self.assertEqual([r["period"] for r in rows], ["2020-01", "2020-05"])

    def test_period_must_be_year_or_month(self):
        with self.assertRaises(ValueError):
            performance.period_returns([], 10_000, "week")


class RiskAdjustedTests(SimpleTestCase):
    def test_under_a_year_is_not_annualized(self):
        ra = performance.risk_adjusted([{"return_pct": 1.0}] * 5)
        self.assertIsNone(ra["sharpe"])
        self.assertEqual(ra["months"], 5)
        self.assertIn("12+", ra["note"])

    def test_sharpe_and_sortino_over_a_full_year(self):
        ra = performance.risk_adjusted([{"return_pct": 2.0}, {"return_pct": -1.0}] * 8)
        self.assertEqual(ra["months"], 16)
        self.assertEqual(ra["positive_months"], 8)
        # mean 0.5, sd 1.549 -> 0.5/1.549*sqrt(12).
        self.assertAlmostEqual(ra["sharpe"], 1.12, places=2)
        # Sortino only penalises downside, so it is the higher of the two here.
        self.assertGreater(ra["sortino"], ra["sharpe"])


class BuyAndHoldTests(SimpleTestCase):
    def test_benchmark_return_and_drawdown(self):
        candles = [{"time": ts(2020), "close": 100},
                   {"time": ts(2021), "close": 50},
                   {"time": ts(2022), "close": 200}]
        bh = performance.buy_and_hold(candles, 10_000)
        self.assertEqual(bh["total_return_pct"], 100.0)
        self.assertEqual(bh["max_drawdown_pct"], 50.0)
        self.assertEqual(bh["end_capital"], 20_000.0)

    def test_window_is_respected(self):
        candles = [{"time": ts(2020), "close": 100},
                   {"time": ts(2021), "close": 50},
                   {"time": ts(2022), "close": 200}]
        bh = performance.buy_and_hold(candles, 10_000,
                                      start_ts=ts(2021), end_ts=ts(2022))
        self.assertEqual(bh["total_return_pct"], 300.0)

    def test_too_little_data(self):
        self.assertIsNone(performance.buy_and_hold([{"time": 0, "close": 1}], 10_000))
        self.assertIsNone(performance.buy_and_hold([], 10_000))


class SummarizeTests(SimpleTestCase):
    def test_shape(self):
        s = performance.summarize([Trade(0, H, 1.0)], 10_000, 1.0)
        self.assertEqual(set(s), {"trade_metrics", "capital", "monthly", "yearly",
                                  "risk_adjusted"})
        self.assertNotIn("points", s["capital"])  # metrics-only callers stay small
        self.assertIn("points", performance.summarize(
            [Trade(0, H, 1.0)], 10_000, 1.0, include_points=True))


class BacktestLiveRTests(SimpleTestCase):
    """``backtest._live_r`` must agree with the buckets ``_record`` fills.

    The curve is built from _live_r while the printed expectancy comes from _record,
    so a divergence between them would show up only as a wrong equity curve — the
    one number nobody can eyeball. This pins them together.
    """

    def _cases(self):
        return [
            {"best_tp": 1, "mfe_pct": 1.0, "mae_pct": 0.0, "cost_r": 0.02},
            {"best_tp": 3, "mfe_pct": 3.0, "mae_pct": -0.5, "cost_r": 0.02},
            {"best_tp": 0, "mfe_pct": 0.2, "mae_pct": -1.0, "cost_r": 0.02},
            {"best_tp": 0, "mfe_pct": 0.3, "mae_pct": -0.4, "cost_r": 0.02,
             "expired": True},
        ]

    def test_matches_record_under_each_exit_model(self):
        from apps.signals.management.commands import backtest as bt

        for model, key in (("tp1", "r_tp1"), ("scaleout", "r_scale")):
            bucket = bt._blank("ALL")
            with mock.patch.object(bt.settings, "SIGNAL_EXIT_MODEL", model):
                live = sum(bt._live_r(dict(c)) for c in self._cases())
            for case in self._cases():
                bt._record(bucket, dict(case))
            self.assertAlmostEqual(live, bucket[key], places=9, msg=model)
