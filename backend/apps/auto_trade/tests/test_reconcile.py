"""Reconciler, invalidation-close, and kill-switch flatten tests."""

from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

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
)


@override_settings(AUTO_TRADE_ENABLED=True)
class ReconcileTests(TestCase):
    def setUp(self):
        self.user = make_pro_user()
        self.symbol = make_symbol()
        self.service = make_service()
        self.signal = make_signal(self.symbol, self.service)
        self.broker = make_broker(self.user)
        self.config = make_config(self.user, move_sl_to_be_after_tp=1)

    def _open_exec(self, qty=20.0, **over):
        defaults = dict(
            user=self.user, signal=self.signal, broker_connection=self.broker,
            status=TradeExecution.Status.OPEN, bybit_symbol="BTCUSDT",
            side="Buy", qty=qty, fill_price=100.0, intended_entry=100.0,
            order_ids={"entry": "E1", "sl": "S1", "tp": ["T1"]},
        )
        defaults.update(over)
        return TradeExecution.objects.create(**defaults)

    def _run(self, fake):
        with mock.patch.object(tasks, "get_client", return_value=fake):
            return tasks.run_reconcile()

    def test_flat_position_marks_closed_with_pnl(self):
        ex = self._open_exec()
        fake = FakeBroker(position=None, realized=42.0)  # flat on exchange
        summary = self._run(fake)
        self.assertEqual(summary["outcomes"].get("closed"), 1)
        ex.refresh_from_db()
        self.assertEqual(ex.status, TradeExecution.Status.CLOSED)
        self.assertEqual(ex.realized_pnl, 42.0)
        self.assertEqual(ex.close_reason, TradeExecution.CloseReason.TP)  # profit ⇒ TP
        self.assertIsNotNone(ex.closed_at)

    def test_flat_position_with_loss_infers_sl(self):
        ex = self._open_exec()
        fake = FakeBroker(position=None, realized=-30.0)
        self._run(fake)
        ex.refresh_from_db()
        self.assertEqual(ex.close_reason, TradeExecution.CloseReason.SL)

    def test_still_open_no_tp_filled_does_not_move_stop(self):
        ex = self._open_exec(qty=20.0)
        # Full position still open (size == original qty).
        fake = FakeBroker(position={"size": 20.0, "side": "Buy", "avg_price": 100.0})
        summary = self._run(fake)
        self.assertEqual(summary["outcomes"].get("still_open"), 1)
        ex.refresh_from_db()
        self.assertFalse(ex.sl_moved_to_be)
        self.assertFalse(fake.has_call("amend"))
        self.assertIsNotNone(ex.last_reconciled_at)

    def test_breakeven_move_after_first_tp_fills(self):
        ex = self._open_exec(qty=20.0)
        # 25% closed → TP1 filled → move SL to break-even (after_tp=1).
        fake = FakeBroker(position={"size": 15.0, "side": "Buy", "avg_price": 100.0})
        summary = self._run(fake)
        self.assertEqual(summary["outcomes"].get("be_moved"), 1)
        ex.refresh_from_db()
        self.assertTrue(ex.sl_moved_to_be)
        amend = [c for c in fake.calls if c[0] == "amend"][0]
        self.assertEqual(amend[3], 100.0)  # moved to entry/fill price

    def test_breakeven_is_idempotent(self):
        ex = self._open_exec(qty=20.0, sl_moved_to_be=True)
        fake = FakeBroker(position={"size": 15.0, "side": "Buy", "avg_price": 100.0})
        self._run(fake)
        self.assertFalse(fake.has_call("amend"))  # already moved — don't re-amend

    def test_unusable_broker_leaves_row_open(self):
        self.broker.permission_verified = False
        self.broker.save()
        ex = self._open_exec()
        summary = self._run(FakeBroker(position=None))
        self.assertEqual(summary["outcomes"].get("no_broker"), 1)
        ex.refresh_from_db()
        self.assertEqual(ex.status, TradeExecution.Status.OPEN)

    def test_disabled_globally_is_noop(self):
        self._open_exec()
        with override_settings(AUTO_TRADE_ENABLED=False):
            summary = tasks.run_reconcile()
        self.assertIn("skipped", summary)


@override_settings(AUTO_TRADE_ENABLED=True)
class InvalidationCloseTests(TestCase):
    def setUp(self):
        self.user = make_pro_user()
        self.symbol = make_symbol()
        self.service = make_service()
        self.signal = make_signal(self.symbol, self.service)
        self.broker = make_broker(self.user)
        make_config(self.user)
        self.ex = TradeExecution.objects.create(
            user=self.user, signal=self.signal, broker_connection=self.broker,
            status=TradeExecution.Status.OPEN, bybit_symbol="BTCUSDT",
            side="Buy", qty=20.0,
        )

    def test_closes_open_execution_for_invalidated_signal(self):
        fake = FakeBroker(realized=-5.0)
        with mock.patch.object(tasks, "get_client", return_value=fake):
            result = tasks.run_invalidation_close([self.signal.id])
        self.assertEqual(result["closed"], 1)
        self.ex.refresh_from_db()
        self.assertEqual(self.ex.status, TradeExecution.Status.CLOSED)
        self.assertEqual(self.ex.close_reason, TradeExecution.CloseReason.INVALIDATED)
        self.assertTrue(fake.has_call("close"))
        self.assertTrue(fake.has_call("cancel"))

    def test_ignores_signals_without_open_positions(self):
        self.ex.status = TradeExecution.Status.CLOSED
        self.ex.save()
        fake = FakeBroker()
        with mock.patch.object(tasks, "get_client", return_value=fake):
            result = tasks.run_invalidation_close([self.signal.id])
        self.assertEqual(result["closed"], 0)
        self.assertFalse(fake.has_call("close"))

    def test_noop_when_feature_off(self):
        with override_settings(AUTO_TRADE_ENABLED=False):
            result = tasks.run_invalidation_close([self.signal.id])
        self.assertEqual(result["closed"], 0)


@override_settings(AUTO_TRADE_ENABLED=True)
class FlattenTests(TestCase):
    def setUp(self):
        self.user = make_pro_user()
        self.symbol = make_symbol()
        self.service = make_service()
        self.broker = make_broker(self.user)

    def _open(self, n):
        for i in range(n):
            sig = make_signal(self.symbol, self.service)
            TradeExecution.objects.create(
                user=self.user, signal=sig, broker_connection=self.broker,
                status=TradeExecution.Status.OPEN, bybit_symbol="BTCUSDT", qty=1.0,
            )

    def test_flatten_closes_all_open_positions(self):
        self._open(3)
        fake = FakeBroker()
        with mock.patch.object(tasks, "get_client", return_value=fake):
            result = tasks.flatten_open_trades(self.user)
        self.assertEqual(result["closed"], 3)
        self.assertEqual(
            TradeExecution.objects.filter(status=TradeExecution.Status.CLOSED).count(), 3
        )

    def test_flatten_close_failure_counts_error_and_continues(self):
        self._open(2)
        fake = FakeBroker(close_raises=True)
        with mock.patch.object(tasks, "get_client", return_value=fake):
            result = tasks.flatten_open_trades(self.user)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result.get("errors"), 2)
        # Rows stay OPEN since the exchange close failed.
        self.assertEqual(
            TradeExecution.objects.filter(status=TradeExecution.Status.OPEN).count(), 2
        )

    def test_flatten_no_open_positions(self):
        result = tasks.flatten_open_trades(self.user)
        self.assertEqual(result["closed"], 0)
