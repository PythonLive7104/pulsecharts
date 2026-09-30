from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.accounts.plans import FREE, PLANS, PRO, STARTER
from apps.signals.quota import strategies_allowed_for


class StrategyDefaultTests(SimpleTestCase):
    def test_every_plan_defaults_to_all_active_strategies(self):
        for plan_key, plan in PLANS.items():
            with self.subTest(plan=plan_key):
                self.assertEqual(plan["strategies"], -1)
                self.assertEqual(plan["default_strategies"], -1)
                user = SimpleNamespace(plan_tier=plan_key, plan_expiry=None)
                self.assertEqual(strategies_allowed_for(user), -1)

    def test_signal_quotas_are_unchanged(self):
        self.assertEqual(PLANS[FREE]["signal_weekly_quota"], 0)
        self.assertEqual(PLANS[STARTER]["signal_weekly_quota"], 400)
        self.assertEqual(PLANS[PRO]["signal_weekly_quota"], -1)