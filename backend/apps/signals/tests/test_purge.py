"""Purge tests — housekeeping must never erase the track record.

A signal a user was delivered (in-app or Telegram) or traded is the record, and
losing ones are exactly the rows a retention sweep would otherwise remove without
anyone noticing (newPRD §5, §15). Undelivered scan output is still purged.
"""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.execution.models import TradeExecution
from apps.market_data.models import Symbol
from apps.signals.models import Signal, SignalDelivery, SignalService, TelegramDelivery
from apps.signals.tasks import never_delivered, run_purge

User = get_user_model()


@override_settings(SIGNAL_RETENTION_DAYS=30, SIGNAL_RETENTION_DAYS_FLAT=0)
class PurgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="u@example.com", password="x")
        self.symbol = Symbol.objects.create(ticker="BTC", hl_coin="BTC")
        self.service = SignalService.objects.create(name="Breakout", slug="breakout")
        self.old = timezone.now() - timedelta(days=90)

    def _signal(self, outcome=Signal.Outcome.SL, generated_at=None, entry=100.0):
        return Signal.objects.create(
            symbol=self.symbol, service=self.service, direction=Signal.Direction.BUY,
            confidence_pct=80, timeframe="1h", generated_at=generated_at or self.old,
            entry_price=entry, stop_loss=97.0, tp1=103.0, tp2=106.0, tp3=109.0,
            risk_pct=3.0, reward_tp1_pct=3.0, reward_tp2_pct=6.0, reward_tp3_pct=9.0,
            risk_reward_tp1=1.0, risk_reward_tp2=2.0, risk_reward_tp3=3.0,
            dollar_risk=3.0, dollar_tp1=3.0, dollar_tp2=6.0, dollar_tp3=9.0,
            outcome=outcome,
        )

    def test_old_undelivered_signal_is_purged(self):
        sig = self._signal()
        run_purge()
        self.assertFalse(Signal.objects.filter(pk=sig.pk).exists())

    def test_in_app_delivered_loss_survives(self):
        sig = self._signal(outcome=Signal.Outcome.SL)
        SignalDelivery.objects.create(user=self.user, signal=sig)
        run_purge()
        self.assertTrue(Signal.objects.filter(pk=sig.pk).exists())
        self.assertTrue(SignalDelivery.objects.filter(signal=sig).exists())

    def test_telegram_delivered_signal_survives(self):
        sig = self._signal(outcome=Signal.Outcome.TP2)
        TelegramDelivery.objects.create(user=self.user, signal=sig)
        run_purge()
        self.assertTrue(Signal.objects.filter(pk=sig.pk).exists())

    def test_traded_signal_survives(self):
        # The execution row outlives a purge by design (SET_NULL); the signal it
        # traded must too, or the ledger loses the call it was placed on.
        sig = self._signal()
        TradeExecution.objects.create(
            user=self.user, signal=sig, bybit_symbol="BTCUSDT", direction="BUY",
        )
        run_purge()
        self.assertTrue(Signal.objects.filter(pk=sig.pk).exists())

    @override_settings(SIGNAL_RETENTION_DAYS_FLAT=1)
    def test_delivered_flat_close_survives_short_flat_window(self):
        # A flat trend-flip close is a 0R trade in avg_r — still part of the record.
        sig = self._signal(outcome=Signal.Outcome.INVALIDATED,
                           generated_at=timezone.now() - timedelta(days=5))
        SignalDelivery.objects.create(user=self.user, signal=sig)
        undelivered = self._signal(outcome=Signal.Outcome.INVALIDATED,
                                   generated_at=timezone.now() - timedelta(days=5),
                                   entry=101.0)
        run_purge()
        self.assertTrue(Signal.objects.filter(pk=sig.pk).exists())
        self.assertFalse(Signal.objects.filter(pk=undelivered.pk).exists())

    def test_never_delivered_filter(self):
        delivered = self._signal(entry=100.0)
        bare = self._signal(entry=101.0)
        SignalDelivery.objects.create(user=self.user, signal=delivered)
        self.assertEqual(list(never_delivered(Signal.objects.all())), [bare])

    def test_purge_all_keeps_delivered(self):
        delivered = self._signal(entry=100.0)
        pending = self._signal(outcome=Signal.Outcome.PENDING, entry=101.0)
        SignalDelivery.objects.create(user=self.user, signal=delivered)
        call_command("purge_signals", "--all", stdout=StringIO())
        self.assertTrue(Signal.objects.filter(pk=delivered.pk).exists())
        self.assertFalse(Signal.objects.filter(pk=pending.pk).exists())

    def test_purge_all_include_delivered_wipes_everything(self):
        delivered = self._signal()
        SignalDelivery.objects.create(user=self.user, signal=delivered)
        call_command("purge_signals", "--all", "--include-delivered", stdout=StringIO())
        self.assertEqual(Signal.objects.count(), 0)

    def test_dedup_keeps_delivered_duplicate(self):
        # Two open calls on the same (symbol, service, timeframe): the older one was
        # already delivered, so it stays on the user's record.
        older = self._signal(outcome=Signal.Outcome.PENDING,
                             generated_at=timezone.now() - timedelta(hours=2))
        newest = self._signal(outcome=Signal.Outcome.PENDING,
                              generated_at=timezone.now(), entry=101.0)
        SignalDelivery.objects.create(user=self.user, signal=older)
        call_command("dedup_signals", "--apply", stdout=StringIO())
        self.assertTrue(Signal.objects.filter(pk=older.pk).exists())
        self.assertTrue(Signal.objects.filter(pk=newest.pk).exists())
