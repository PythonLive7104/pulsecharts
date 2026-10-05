"""Exit model — full exit at TP1 vs the 50/25/25 scale-out.

Each Signal is managed, resolved and scored under the plan it was issued with, so
switching SIGNAL_EXIT_MODEL changes new signals only and never rescores a trade a
user already took under the other plan.
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.market_data.models import Symbol
from apps.signals import tasks
from apps.signals.models import Signal, SignalService
from apps.signals.stats import accuracy_stats, outcome_r, trade_r, win_r

HOUR = 3600


class RTests(TestCase):
    def test_full_exit_wins_are_one_r_whatever_ran_further(self):
        for best in (1, 2, 3):
            self.assertEqual(win_r("tp1", best), 1.0)

    def test_scaleout_ladder_unchanged(self):
        self.assertEqual([win_r("scaleout", b) for b in (1, 2, 3)], [0.5, 1.0, 1.75])

    def test_losses_and_flats(self):
        self.assertEqual(trade_r("tp1", 0), -1.0)
        self.assertEqual(outcome_r("tp1", "SL"), -1.0)
        self.assertEqual(outcome_r("tp1", "INVALID"), 0.0)
        self.assertEqual(outcome_r("scaleout", "TP2"), 1.0)

    def test_unknown_model_scores_as_scaleout(self):
        self.assertEqual(win_r("legacy", 1), 0.5)


class _Base(TestCase):
    def setUp(self):
        self.symbol = Symbol.objects.create(ticker="BTC", hl_coin="BTC")
        self.service = SignalService.objects.create(name="Breakout", slug="bollinger-breakout")

    def _signal(self, exit_model=None, **over):
        fields = dict(
            symbol=self.symbol, service=self.service, direction=Signal.Direction.BUY,
            confidence_pct=80, timeframe="4h",
            generated_at=timezone.now() - timezone.timedelta(days=3),
            entry_price=100.0, stop_loss=97.0, tp1=103.0, tp2=106.0, tp3=109.0,
            risk_pct=3.0, reward_tp1_pct=3.0, reward_tp2_pct=6.0, reward_tp3_pct=9.0,
            risk_reward_tp1=1.0, risk_reward_tp2=2.0, risk_reward_tp3=3.0,
            dollar_risk=3.0, dollar_tp1=3.0, dollar_tp2=6.0, dollar_tp3=9.0,
            confluence_count=1,  # the push path always sets this before formatting
        )
        if exit_model:
            fields["exit_model"] = exit_model
        fields.update(over)
        return Signal.objects.create(**fields)


class DefaultTests(_Base):
    @override_settings(SIGNAL_EXIT_MODEL="tp1")
    def test_new_signal_takes_the_setting(self):
        self.assertEqual(self._signal().exit_model, "tp1")

    def test_switching_the_setting_never_rescores_old_trades(self):
        with override_settings(SIGNAL_EXIT_MODEL="scaleout"):
            old = self._signal(outcome=Signal.Outcome.TP1, best_tp=1)
        with override_settings(SIGNAL_EXIT_MODEL="tp1"):
            old.refresh_from_db()
            self.assertEqual(old.exit_model, "scaleout")
            stats = accuracy_stats(Signal.objects.all())
        self.assertEqual(stats["overall"]["avg_r"], 0.5)


class EvaluationTests(_Base):
    """run_evaluation with candles that tag TP1 and stall below TP2."""

    def _evaluate(self, sig):
        start = int(sig.generated_at.timestamp()) + HOUR
        candles = [  # 4h bars: TP1 (103) tags on the second bar, TP2 (106) never
            {"time": start + k * 4 * HOUR, "open": o, "high": h, "low": l, "close": c}
            for k, (o, h, l, c) in enumerate([
                (100, 101, 99, 100.5), (100.5, 103.5, 100, 103), (103, 104, 101, 102),
            ])
        ]
        with mock.patch.object(tasks, "get_candles_since", return_value=candles):
            tasks.run_evaluation()
        sig.refresh_from_db()
        return sig

    def test_full_exit_resolves_at_tp1(self):
        sig = self._evaluate(self._signal(exit_model="tp1"))
        self.assertEqual(sig.outcome, Signal.Outcome.TP1)
        self.assertIsNotNone(sig.resolved_at)

    def test_scaleout_keeps_running_after_tp1(self):
        sig = self._evaluate(self._signal(exit_model="scaleout"))
        self.assertEqual(sig.outcome, Signal.Outcome.PENDING)
        self.assertEqual(sig.best_tp, 1)


class StatsTests(_Base):
    def test_mixed_models_each_scored_their_own_way(self):
        self._signal(exit_model="tp1", outcome=Signal.Outcome.TP1, best_tp=1, entry_price=100.0)
        self._signal(exit_model="scaleout", outcome=Signal.Outcome.TP1, best_tp=1, entry_price=101.0)
        self._signal(exit_model="tp1", outcome=Signal.Outcome.SL, entry_price=102.0)
        overall = accuracy_stats(Signal.objects.all())["overall"]
        # (+1.0 + 0.5 - 1.0) / 3 trades
        self.assertAlmostEqual(overall["avg_r"], round(0.5 / 3, 3))
        self.assertEqual((overall["wins"], overall["losses"]), (2, 1))


class TelegramCopyTests(_Base):
    def test_full_exit_card_shows_one_target_and_the_plan(self):
        text = tasks.format_signal_for_telegram(self._signal(exit_model="tp1"))
        self.assertIn("Target <b>", text)
        self.assertNotIn("TP2", text)
        self.assertIn("Close the whole position at the target", text)

    def test_scaleout_card_unchanged(self):
        text = tasks.format_signal_for_telegram(self._signal(exit_model="scaleout"))
        self.assertIn("TP2", text)
        self.assertIn("let the rest run", text)

    def test_full_exit_closure_says_closed_in_full(self):
        sig = self._signal(exit_model="tp1", outcome=Signal.Outcome.TP1, best_tp=1)
        text = tasks.format_closure_for_telegram(sig)
        self.assertIn("hit the target", text)
        self.assertIn("closed in full", text)
        self.assertNotIn("runner", text)
