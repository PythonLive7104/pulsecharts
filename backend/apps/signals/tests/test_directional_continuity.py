from datetime import datetime, timedelta, timezone
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone as django_timezone

from apps.market_data.models import Symbol
from apps.signals.models import Signal, SignalService
from apps.signals.tasks import (
    _invalidate_opposite_pending,
    _same_direction_repeat_ready,
    _scan_direction_allows,
)


class SameDirectionRepeatTests(SimpleTestCase):
    def test_repeat_waits_for_timeframe_bar_cooldown(self):
        last = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

        self.assertFalse(
            _same_direction_repeat_ready(last, last + timedelta(hours=11), "4h", 3)
        )
        self.assertTrue(
            _same_direction_repeat_ready(last, last + timedelta(hours=12), "4h", 3)
        )

    def test_missing_previous_call_allows_first_signal(self):
        self.assertTrue(_same_direction_repeat_ready(None, django_timezone.now(), "1d", 3))

    def test_only_one_direction_is_accepted_per_symbol_per_scan(self):
        accepted = {7: Signal.Direction.BUY}

        self.assertTrue(_scan_direction_allows(accepted, 7, Signal.Direction.BUY))
        self.assertFalse(_scan_direction_allows(accepted, 7, Signal.Direction.SELL))
        self.assertTrue(_scan_direction_allows(accepted, 8, Signal.Direction.SELL))


class OppositeDirectionInvalidationTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:10]
        self.symbol = Symbol.objects.create(ticker=f"TEST-{suffix}", hl_coin=f"T{suffix}")
        self.service_a = SignalService.objects.create(
            name="Momentum", slug=f"test-momentum-{suffix}"
        )
        self.service_b = SignalService.objects.create(
            name="Breakout", slug=f"test-breakout-{suffix}"
        )

    def _signal(self, service, direction, timeframe):
        return Signal.objects.create(
            symbol=self.symbol,
            service=service,
            direction=direction,
            confidence_pct=85,
            timeframe=timeframe,
            generated_at=django_timezone.now(),
            entry_price=100.0,
            stop_loss=90.0,
            tp1=110.0,
            tp2=120.0,
            tp3=130.0,
            tp4=None,
            risk_pct=10.0,
            reward_tp1_pct=10.0,
            reward_tp2_pct=20.0,
            reward_tp3_pct=30.0,
            reward_tp4_pct=None,
            risk_reward_tp1=1.0,
            risk_reward_tp2=2.0,
            risk_reward_tp3=3.0,
            risk_reward_tp4=None,
            dollar_risk=10.0,
            dollar_tp1=10.0,
            dollar_tp2=20.0,
            dollar_tp3=30.0,
            dollar_tp4=None,
        )

    def test_flip_invalidates_opposite_calls_across_services_and_timeframes(self):
        old_4h = self._signal(self.service_a, Signal.Direction.BUY, "4h")
        old_daily = self._signal(self.service_b, Signal.Direction.BUY, "1d")
        same_direction = self._signal(self.service_a, Signal.Direction.SELL, "1h")
        resolved_at = django_timezone.now()

        count = _invalidate_opposite_pending(self.symbol, Signal.Direction.SELL, resolved_at)

        self.assertEqual(count, 2)
        old_4h.refresh_from_db()
        old_daily.refresh_from_db()
        same_direction.refresh_from_db()
        self.assertEqual(old_4h.outcome, Signal.Outcome.INVALIDATED)
        self.assertEqual(old_daily.outcome, Signal.Outcome.INVALIDATED)
        self.assertEqual(old_4h.resolved_at, resolved_at)
        self.assertEqual(same_direction.outcome, Signal.Outcome.PENDING)

    def test_custom_flip_does_not_invalidate_other_services(self):
        user = get_user_model().objects.create_user(email=f"{uuid4().hex}@example.com")
        custom = SignalService.objects.create(
            name="Private custom",
            slug=f"private-{uuid4().hex[:10]}",
            owner=user,
            rule_config={"conditions": []},
        )
        old_custom = self._signal(custom, Signal.Direction.BUY, "4h")
        old_builtin = self._signal(self.service_a, Signal.Direction.BUY, "1d")

        count = _invalidate_opposite_pending(
            self.symbol, Signal.Direction.SELL, django_timezone.now(), service=custom
        )

        self.assertEqual(count, 1)
        old_custom.refresh_from_db()
        old_builtin.refresh_from_db()
        self.assertEqual(old_custom.outcome, Signal.Outcome.INVALIDATED)
        self.assertEqual(old_builtin.outcome, Signal.Outcome.PENDING)