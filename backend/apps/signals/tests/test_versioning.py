"""Strategy versioning — every signal is attributable to the exact rules behind it.

The record is only meaningful per version (newPRD §3 P2): a result has to be
traceable to, and reproducible from, the settings and code that produced it. These
pin when a version is minted, when it is NOT, and that the scan stamps it.
"""

import ast
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import RestrictedError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.market_data.models import Symbol
from apps.signals import tasks, versioning
from apps.signals.models import Signal, SignalService, StrategyVersion
from apps.watchlists.models import WatchlistItem

User = get_user_model()


class VersionForTests(TestCase):
    def setUp(self):
        self.service = SignalService.objects.create(name="Breakout", slug="bollinger-breakout")

    def test_same_rules_reuse_one_version(self):
        a = versioning.version_for(self.service)
        b = versioning.version_for(self.service)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(a.number, 1)
        self.assertEqual(StrategyVersion.objects.count(), 1)

    def test_rule_setting_change_mints_next_version(self):
        v1 = versioning.version_for(self.service)
        with override_settings(SIGNAL_ADX_MIN=47.5):
            v2 = versioning.version_for(self.service)
        self.assertEqual(v2.number, 2)
        self.assertNotEqual(v1.fingerprint, v2.fingerprint)

    def test_reverting_a_setting_returns_the_earlier_version(self):
        # Rules are identified by content, not by history: going back to v1's exact
        # configuration is v1 again, not a v3 that happens to match it.
        v1 = versioning.version_for(self.service)
        with override_settings(SIGNAL_REENTRY_COOLDOWN_BARS=987):
            versioning.version_for(self.service)
        self.assertEqual(versioning.version_for(self.service).pk, v1.pk)

    def test_business_setting_does_not_split_the_record(self):
        v1 = versioning.version_for(self.service)
        with override_settings(SIGNAL_FREE_TRIAL_DAYS=99, SIGNAL_SHADOW_MODE=True):
            self.assertEqual(versioning.version_for(self.service).pk, v1.pk)

    def test_stop_geometry_is_a_rule(self):
        v1 = versioning.version_for(self.service)
        with override_settings(SIGNAL_ATR_STOP_FLOOR={"crypto": 9.9, "forex": 9.9}):
            self.assertEqual(versioning.version_for(self.service).number, v1.number + 1)

    def test_roster_change_mints_a_version(self):
        # Confluence is scored across the active strategies, so turning one on
        # changes what every strategy delivers.
        v1 = versioning.version_for(self.service)
        SignalService.objects.create(name="Other", slug="volatility-breakout")
        self.assertEqual(versioning.version_for(self.service).number, v1.number + 1)

    def test_custom_rule_change_mints_a_version(self):
        owner = User.objects.create_user(email="o@example.com", password="x")
        custom = SignalService.objects.create(
            name="Mine", slug="mine", owner=owner, rule_config={"rsi_below": 30},
        )
        v1 = versioning.version_for(custom)
        custom.rule_config = {"rsi_below": 25}
        custom.save()
        self.assertEqual(versioning.version_for(custom).number, v1.number + 1)

    def test_versions_number_per_strategy(self):
        other = SignalService.objects.create(name="Other", slug="volatility-breakout")
        self.assertEqual(versioning.version_for(self.service).number, 1)
        self.assertEqual(versioning.version_for(other).number, 1)


class CodeHashTests(TestCase):
    def _dump(self, src):
        return ast.dump(versioning._strip_docstrings(ast.parse(src)), include_attributes=False)

    def test_comments_and_docstrings_do_not_count(self):
        a = 'def f(x):\n    """Old words."""\n    return x > 25  # floor\n'
        b = 'def f(x):\n    """New, better words."""\n    # a new comment\n    return x > 25\n'
        self.assertEqual(self._dump(a), self._dump(b))

    def test_logic_counts(self):
        self.assertNotEqual(self._dump("def f(x):\n    return x > 25\n"),
                            self._dump("def f(x):\n    return x > 30\n"))

    def test_hash_covers_every_rule_module(self):
        # A renamed/missing rule module must fail loudly, not silently drop out of
        # the fingerprint.
        versioning.code_hash.cache_clear()
        self.assertEqual(len(versioning.code_hash()), 64)

    def test_tasks_rule_names_all_exist(self):
        tree = ast.parse((versioning._APP_DIR / "tasks.py").read_text(encoding="utf-8"))
        names = {versioning._top_level_name(n) for n in tree.body}
        self.assertEqual(versioning.TASKS_RULE_NAMES - names, set())


class IntegrityTests(TestCase):
    def setUp(self):
        self.symbol = Symbol.objects.create(ticker="BTC", hl_coin="BTC")

    def _signal(self, service, version):
        return Signal.objects.create(
            symbol=self.symbol, service=service, strategy_version=version,
            direction=Signal.Direction.BUY, confidence_pct=80, timeframe="1h",
            generated_at=timezone.now(), entry_price=100.0, stop_loss=97.0,
            tp1=103.0, tp2=106.0, tp3=109.0, risk_pct=3.0, reward_tp1_pct=3.0,
            reward_tp2_pct=6.0, reward_tp3_pct=9.0, risk_reward_tp1=1.0,
            risk_reward_tp2=2.0, risk_reward_tp3=3.0, dollar_risk=3.0,
            dollar_tp1=3.0, dollar_tp2=6.0, dollar_tp3=9.0,
        )

    def test_version_with_signals_cannot_be_deleted(self):
        svc = SignalService.objects.create(name="Breakout", slug="bollinger-breakout")
        version = versioning.version_for(svc)
        self._signal(svc, version)
        with self.assertRaises(RestrictedError):
            version.delete()

    def test_deleting_a_custom_strategy_still_works(self):
        owner = User.objects.create_user(email="o@example.com", password="x")
        custom = SignalService.objects.create(name="Mine", slug="mine", owner=owner)
        self._signal(custom, versioning.version_for(custom))
        custom.delete()
        self.assertEqual(StrategyVersion.objects.count(), 0)
        self.assertEqual(Signal.objects.count(), 0)

    def test_signal_config_reports_versions(self):
        svc = SignalService.objects.create(name="Breakout", slug="bollinger-breakout")
        out = StringIO()
        call_command("signal_config", stdout=out)
        self.assertIn("NEW — minted on its next signal", out.getvalue())
        versioning.version_for(svc)
        out = StringIO()
        call_command("signal_config", stdout=out)
        self.assertIn("bollinger-breakout", out.getvalue())
        self.assertIn(" v1 ", out.getvalue())
        self.assertEqual(StrategyVersion.objects.count(), 1)  # report never writes


@override_settings(
    SIGNAL_TIMEFRAMES=["1h"], SIGNAL_REGIME_FILTER_ENABLED=False,
    SIGNAL_HTF_STRUCTURE_ENABLED=False, SIGNAL_EXIT_ON_TREND_BREAK=False,
    SIGNAL_SKIP_CRYPTO_WEEKEND=False, SIGNAL_REENTRY_COOLDOWN_BARS=0,
)
class ScanStampsVersionTests(TestCase):
    """run_scan with market data and the engine mocked: only the stamping is real."""

    def setUp(self):
        user = User.objects.create_user(email="u@example.com", password="x")
        self.symbol = Symbol.objects.create(ticker="BTC", hl_coin="BTC")
        WatchlistItem.objects.create(user=user, symbol=self.symbol)
        self.service = SignalService.objects.create(name="Breakout", slug="bollinger-breakout")

    def _scan(self, entry=100.0):
        bar = 3600
        start = int(timezone.now().timestamp()) - 400 * bar
        candles = [{"time": start + i * bar, "open": 1, "high": 1, "low": 1,
                    "close": 1, "volume": 1} for i in range(300)]
        sig = dict(
            direction="BUY", confidence_pct=80, entry_price=entry, stop_loss=97.0,
            tp1=103.0, tp2=106.0, tp3=109.0, risk_pct=3.0, reward_tp1_pct=3.0,
            reward_tp2_pct=6.0, reward_tp3_pct=9.0, risk_reward_tp1=1.0,
            risk_reward_tp2=2.0, risk_reward_tp3=3.0, dollar_risk=3.0,
            dollar_tp1=3.0, dollar_tp2=6.0, dollar_tp3=9.0,
            reasoning="", invalidation="",
        )
        with mock.patch.object(tasks, "get_candles", return_value=candles), \
             mock.patch.object(tasks, "compute_indicators",
                               return_value={"close": 100.0, "ema200": 90.0}), \
             mock.patch.object(tasks, "candidate_direction_for_service", return_value="BUY"), \
             mock.patch.object(tasks, "generate_signal", return_value=sig), \
             mock.patch.object(tasks, "leader_trend", return_value=None), \
             mock.patch.object(tasks, "forex_market_open", return_value=True):
            return tasks.run_scan()

    def test_new_signal_carries_its_version(self):
        result = self._scan()
        self.assertEqual(result["created"], 1)
        sig = Signal.objects.get()
        self.assertIsNotNone(sig.strategy_version)
        self.assertEqual(sig.strategy_version.service, self.service)
        self.assertEqual(sig.strategy_version.number, 1)

    def test_unchanged_rules_keep_stamping_the_same_version(self):
        self._scan(entry=100.0)
        Signal.objects.update(outcome=Signal.Outcome.SL)  # close it so a new one fires
        self._scan(entry=101.0)
        self.assertEqual(Signal.objects.count(), 2)
        self.assertEqual(
            Signal.objects.values("strategy_version").distinct().count(), 1
        )
