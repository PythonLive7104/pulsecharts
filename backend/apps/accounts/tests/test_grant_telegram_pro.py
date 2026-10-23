"""`grant_telegram_pro` hands the Telegram promo out without downgrading anyone.

The two rules worth a test: a perpetual-access account (the admin, a lifetime buyer)
must never come out of this with a plan_expiry, and a user who already holds longer
access must keep it. Both failure modes are silent — they'd only show up a month
later when someone's plan lapsed — so they're pinned here.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.plans import FREE, PRO, STARTER
from apps.market_data.models import Symbol
from apps.signals.models import SignalService, UserSignalSubscription
from apps.watchlists.models import WatchlistItem

User = get_user_model()


class GrantTelegramProTests(TestCase):
    def setUp(self):
        Symbol.objects.create(
            ticker="BTC-USD", display_name="Bitcoin", hl_coin="BTC", sort_order=1
        )
        Symbol.objects.create(
            ticker="EUR-USD", display_name="Euro", feed_symbol="EURUSD=X",
            asset_class=Symbol.AssetClass.FOREX, sort_order=2,
        )
        SignalService.objects.create(
            name="Momentum Crossover", slug="momentum-crossover",
            strategy_type="momentum", is_active=True,
        )

    def _user(self, email, **extra):
        extra.setdefault("telegram_chat_id", "123")
        extra.setdefault("telegram_active", True)
        return User.objects.create_user(email=email, password="x", **extra)

    def test_connected_user_gets_pro_and_the_full_symbol_set(self):
        user = self._user("linked@example.com")

        call_command("grant_telegram_pro", verbosity=0)

        user.refresh_from_db()
        self.assertEqual(user.plan_tier, PRO)
        self.assertAlmostEqual(
            user.plan_expiry, timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )
        # Every active symbol, forex included, plus every active strategy.
        self.assertEqual(WatchlistItem.objects.filter(user=user).count(), 2)
        self.assertEqual(UserSignalSubscription.objects.filter(user=user).count(), 1)

    def test_perpetual_and_staff_accounts_keep_their_plan(self):
        admin = self._user(
            "admin@example.com", is_staff=True, plan_tier=PRO, plan_expiry=None
        )
        lifetime = self._user("lifetime@example.com", plan_tier=PRO, plan_expiry=None)

        call_command("grant_telegram_pro", "--include-staff", verbosity=0)

        for user in (admin, lifetime):
            user.refresh_from_db()
            self.assertEqual(user.plan_tier, PRO)
            self.assertIsNone(user.plan_expiry, f"{user.email} was given an expiry")
        # Still seeded, so the promo's signals reach them.
        self.assertEqual(WatchlistItem.objects.filter(user=lifetime).count(), 2)

    def test_staff_are_skipped_by_default(self):
        admin = self._user("admin@example.com", is_superuser=True, plan_tier=FREE)

        call_command("grant_telegram_pro", verbosity=0)

        admin.refresh_from_db()
        self.assertEqual(admin.plan_tier, FREE)

    def test_longer_existing_access_is_never_shortened(self):
        later = timezone.now() + timedelta(days=90)
        user = self._user("annual@example.com", plan_tier=STARTER, plan_expiry=later)

        call_command("grant_telegram_pro", verbosity=0)

        user.refresh_from_db()
        # Raised to Pro, but the date they already paid for stands.
        self.assertEqual(user.plan_tier, PRO)
        self.assertEqual(user.plan_expiry, later)

    def test_stale_expiry_on_a_free_row_does_not_extend_the_grant(self):
        # plan_key ignores an expiry on a free tier, so it isn't access they hold
        # and must not be carried into the promo window.
        user = self._user(
            "stale@example.com", plan_tier=FREE,
            plan_expiry=timezone.now() + timedelta(days=400),
        )

        call_command("grant_telegram_pro", verbosity=0)

        user.refresh_from_db()
        self.assertEqual(user.plan_tier, PRO)
        self.assertAlmostEqual(
            user.plan_expiry, timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )

    def test_users_without_telegram_are_untouched(self):
        user = User.objects.create_user(email="noteleg@example.com", password="x")

        call_command("grant_telegram_pro", verbosity=0)

        user.refresh_from_db()
        self.assertEqual(user.plan_tier, FREE)
        self.assertFalse(WatchlistItem.objects.filter(user=user).exists())

    def test_all_flag_grants_users_without_telegram(self):
        plain = User.objects.create_user(email="plain@example.com", password="x")
        linked = self._user("linked@example.com")

        call_command("grant_telegram_pro", "--all", verbosity=0)

        for user in (plain, linked):
            user.refresh_from_db()
            self.assertEqual(user.plan_tier, PRO, user.email)
            self.assertEqual(WatchlistItem.objects.filter(user=user).count(), 2)

    def test_all_flag_still_protects_staff_and_perpetual(self):
        admin = User.objects.create_user(
            email="admin@example.com", password="x", is_staff=True, plan_tier=PRO
        )
        lifetime = User.objects.create_user(
            email="lifetime@example.com", password="x", plan_tier=PRO, plan_expiry=None
        )

        call_command("grant_telegram_pro", "--all", verbosity=0)

        for user in (admin, lifetime):
            user.refresh_from_db()
            self.assertIsNone(user.plan_expiry, f"{user.email} was given an expiry")

    def test_dry_run_writes_nothing(self):
        user = self._user("dry@example.com")

        call_command("grant_telegram_pro", "--dry-run", verbosity=0)

        user.refresh_from_db()
        self.assertEqual(user.plan_tier, FREE)
        self.assertFalse(WatchlistItem.objects.filter(user=user).exists())
