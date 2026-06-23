"""Execution-engine tests: eligibility, guards, idempotency, and the naked-
position safety net. The broker is faked and injected via get_client."""

from unittest import mock

from django.test import TestCase, override_settings

from apps.auto_trade import tasks
from apps.auto_trade.models import TradeExecution

from ._factories import (
    FakeBroker,
    make_broker,
    make_config,
    make_pro_user,
    make_service,
    make_signal,
    make_symbol,
    subscribe_and_watch,
)


@override_settings(AUTO_TRADE_ENABLED=True, SIGNAL_MIN_CONFIDENCE=65)
class RunExecutionTests(TestCase):
    def setUp(self):
        self.user = make_pro_user()
        self.symbol = make_symbol()
        self.service = make_service()
        self.signal = make_signal(self.symbol, self.service)
        self.config = make_config(self.user)
        self.broker = make_broker(self.user)
        subscribe_and_watch(self.user, self.signal)
        self.fake = FakeBroker()

    def _run(self):
        with mock.patch.object(tasks, "get_client", return_value=self.fake):
            return tasks.run_execution(self.signal.id)

    # --- happy path ---
    def test_places_entry_and_protection_and_opens_row(self):
        summary = self._run()
        self.assertEqual(summary["placed"], 1)
        ex = TradeExecution.objects.get(user=self.user, signal=self.signal)
        self.assertEqual(ex.status, TradeExecution.Status.OPEN)
        self.assertEqual(ex.side, "Buy")
        self.assertEqual(ex.order_ids["entry"], "E1")
        self.assertEqual(ex.order_ids["sl"], "S1")
        self.assertTrue(self.fake.has_call("entry"))
        self.assertTrue(self.fake.has_call("protect"))
        # 1% of 10k / $5 stop distance = 20 units.
        self.assertAlmostEqual(ex.qty, 20.0)

    # --- guards ---
    def test_global_flag_off_is_noop(self):
        with override_settings(AUTO_TRADE_ENABLED=False):
            summary = self._run()
        self.assertIn("skipped", summary)
        self.assertEqual(TradeExecution.objects.count(), 0)

    def test_non_pro_user_skipped(self):
        self.user.plan_tier = "starter"
        self.user.save()
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("not_pro"), 1)
        self.assertEqual(TradeExecution.objects.count(), 0)

    def test_unusable_broker_skipped(self):
        self.broker.permission_verified = False
        self.broker.save()
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("no_broker"), 1)

    def test_unmapped_symbol_skipped_and_recorded(self):
        self.symbol.bybit_symbol = ""
        self.symbol.save()
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("unmapped"), 1)
        ex = TradeExecution.objects.get()
        self.assertEqual(ex.status, TradeExecution.Status.SKIPPED)

    def test_low_confidence_skipped(self):
        self.config.min_confidence = 90
        self.config.save()
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("low_confidence"), 1)
        self.assertFalse(self.fake.has_call("entry"))

    def test_slippage_guard_skips_when_price_ran_away(self):
        self.fake.last_price = 100.0 * 1.02  # 2% past the 0.5% default tolerance
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("slippage"), 1)
        ex = TradeExecution.objects.get()
        self.assertEqual(ex.status, TradeExecution.Status.SKIPPED)
        self.assertFalse(self.fake.has_call("entry"))

    def test_stale_signal_skipped(self):
        self.config.max_signal_age_sec = 1
        self.config.save()
        from django.utils import timezone
        from datetime import timedelta
        self.signal.generated_at = timezone.now() - timedelta(seconds=120)
        self.signal.save()
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("stale"), 1)

    def test_daily_cap_blocks_further_trades(self):
        self.config.max_daily_trades = 1
        self.config.save()
        # Pre-existing trade today on a different signal hits the cap.
        other = make_signal(self.symbol, self.service)
        TradeExecution.objects.create(
            user=self.user, signal=other, broker_connection=self.broker,
            status=TradeExecution.Status.OPEN,
        )
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("daily_cap"), 1)

    def test_position_cap_blocks_further_trades(self):
        self.config.max_open_positions = 1
        self.config.max_daily_trades = 10
        self.config.save()
        other = make_signal(self.symbol, self.service)
        TradeExecution.objects.create(
            user=self.user, signal=other, broker_connection=self.broker,
            status=TradeExecution.Status.OPEN,
        )
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("position_cap"), 1)

    # --- idempotency ---
    def test_idempotent_second_run_does_not_double_place(self):
        self._run()
        # Second run with a fresh fake — must not place again.
        self.fake = FakeBroker()
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("dup"), 1)
        self.assertEqual(
            TradeExecution.objects.filter(user=self.user, signal=self.signal).count(), 1
        )
        self.assertFalse(self.fake.has_call("entry"))

    # --- safety: protection failure flattens the naked entry ---
    def test_protection_failure_closes_position_and_marks_error(self):
        self.fake.protective_raises = True
        summary = self._run()
        self.assertEqual(summary["outcomes"].get("protection_failed"), 1)
        ex = TradeExecution.objects.get()
        self.assertEqual(ex.status, TradeExecution.Status.ERROR)
        # The entry filled, protection failed → we must have closed the position.
        self.assertTrue(self.fake.has_call("close"))

    def test_non_directional_signal_skipped(self):
        self.signal.direction = "NEUTRAL"
        self.signal.save()
        summary = self._run()
        self.assertIn("skipped", summary)
        self.assertEqual(TradeExecution.objects.count(), 0)
