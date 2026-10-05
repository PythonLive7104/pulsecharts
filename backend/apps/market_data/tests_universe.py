"""Signal universe — signals only on the most liquid coins, re-ranked by volume."""

import time
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.market_data import universe
from apps.market_data.models import MarketContext, Symbol

COINS = [f"C{i:02d}" for i in range(30)]  # C00 = most liquid


@override_settings(SIGNAL_UNIVERSE_TOP_N=5, SIGNAL_UNIVERSE_MIN_HISTORY_DAYS=0)
class UniverseTests(TestCase):
    def setUp(self):
        for i, coin in enumerate(COINS):
            Symbol.objects.create(ticker=coin, hl_coin=coin, sort_order=i)
        self.fx = Symbol.objects.create(
            ticker="EUR-USD", hl_coin="", asset_class=Symbol.AssetClass.FOREX,
            feed_symbol="EURUSD=X",
        )

    def _enabled(self):
        return set(Symbol.objects.filter(
            signals_enabled=True, asset_class=Symbol.AssetClass.CRYPTO
        ).values_list("hl_coin", flat=True))

    def test_only_top_n_get_signals(self):
        result = universe.apply_signal_universe(ranking=COINS)
        self.assertEqual(self._enabled(), set(COINS[:5]))
        self.assertEqual(len(result["switched_off"]), 25)

    def test_forex_is_untouched(self):
        universe.apply_signal_universe(ranking=COINS)
        self.fx.refresh_from_db()
        self.assertTrue(self.fx.signals_enabled)

    def test_hysteresis_keeps_a_member_that_slips_slightly(self):
        universe.apply_signal_universe(ranking=COINS)
        # C04 slips from #5 to #7: still within 5 * 1.5 = 7 slots, so it stays…
        slipped = COINS[:4] + COINS[5:7] + [COINS[4]] + COINS[7:]
        universe.apply_signal_universe(ranking=slipped)
        self.assertIn("C04", self._enabled())
        # …but a coin that wasn't a member needs a genuine top-5 rank to join.
        self.assertNotIn("C06", self._enabled())

    def test_member_that_falls_far_is_dropped(self):
        universe.apply_signal_universe(ranking=COINS)
        fallen = COINS[:4] + COINS[5:20] + [COINS[4]] + COINS[20:]
        universe.apply_signal_universe(ranking=fallen)
        self.assertNotIn("C04", self._enabled())
        self.assertIn("C05", self._enabled())

    def test_thin_ranking_changes_nothing(self):
        result = universe.apply_signal_universe(ranking=COINS[:3])
        self.assertIn("skipped", result)
        self.assertEqual(len(self._enabled()), 30)

    def test_dry_run_changes_nothing(self):
        result = universe.apply_signal_universe(ranking=COINS, dry_run=True)
        self.assertEqual(len(result["switched_off"]), 25)
        self.assertEqual(len(self._enabled()), 30)

    @override_settings(SIGNAL_UNIVERSE_TOP_N=0)
    def test_zero_is_off(self):
        self.assertIn("skipped", universe.apply_signal_universe(ranking=COINS))
        self.assertEqual(len(self._enabled()), 30)

    def test_ranks_on_recorded_seven_day_volume(self):
        now = timezone.now()
        for i, coin in enumerate(COINS):
            sym = Symbol.objects.get(hl_coin=coin)
            MarketContext.objects.create(symbol=sym, bucket=now - timedelta(hours=1),
                                         day_ntl_vlm=1_000_000 - i)
        # Older than the window: must not count, however large.
        MarketContext.objects.create(symbol=Symbol.objects.get(hl_coin="C29"),
                                     bucket=now - timedelta(days=30), day_ntl_vlm=10**12)
        ranked, source = universe.ranked_coins(now)
        self.assertEqual(ranked[:3], ["C00", "C01", "C02"])
        self.assertIn("7-day", source)

    def test_falls_back_to_live_volume_without_history(self):
        live = {coin: {"dayNtlVlm": str(1000 - i)} for i, coin in enumerate(COINS)}
        with mock.patch("apps.market_data.client.fetch_asset_contexts", return_value=live):
            ranked, source = universe.ranked_coins()
        self.assertEqual(ranked[0], "C00")
        self.assertIn("live", source)

    def test_command_reports_and_applies(self):
        with mock.patch.object(universe, "ranked_coins", return_value=(COINS, "test")):
            out = StringIO()
            call_command("set_signal_universe", stdout=out)
        self.assertIn("Switched OFF (25)", out.getvalue())
        self.assertEqual(self._enabled(), set(COINS[:5]))


@override_settings(SIGNAL_UNIVERSE_TOP_N=5, SIGNAL_UNIVERSE_MIN_HISTORY_DAYS=365)
class HistoryRequirementTests(TestCase):
    """A brand-new launch topping the volume chart waits for a track record."""

    def setUp(self):
        for coin in COINS:
            Symbol.objects.create(ticker=coin, hl_coin=coin)

    def test_new_coin_gives_its_slot_to_the_next_established_one(self):
        young = {"C01", "C03"}
        age = lambda coin, need: coin not in young  # noqa: E731
        result = universe.apply_signal_universe(ranking=COINS, age_fn=age)
        self.assertEqual(result["universe"], ["C00", "C02", "C04", "C05", "C06"])
        self.assertEqual(result["too_new"], ["C01", "C03"])

    def test_failed_age_lookup_changes_nothing(self):
        import requests

        def boom(coin, need):
            raise requests.ConnectionError("down")

        with self.assertRaises(requests.ConnectionError):
            universe.apply_signal_universe(ranking=COINS, age_fn=boom)
        self.assertEqual(Symbol.objects.filter(signals_enabled=False).count(), 0)

    def test_age_probe_is_one_small_window_a_year_back(self):
        from django.core.cache import cache

        cache.delete("universe:old_enough:C00:365")
        with mock.patch("apps.market_data.client.fetch_candle_window",
                        return_value=[{"t": 1}]) as fetch:
            self.assertTrue(universe.old_enough("C00", 365, sleep=lambda s: None))
            # Second call is served from cache: no request at all.
            self.assertTrue(universe.old_enough("C00", 365, sleep=lambda s: None))
        self.assertEqual(fetch.call_count, 1)
        _coin, _iv, start_ms, end_ms = fetch.call_args.args
        days_back = (time.time() * 1000 - end_ms) / 86_400_000
        self.assertAlmostEqual(days_back, 365, delta=0.01)
        self.assertEqual((end_ms - start_ms) / 86_400_000, 7)

    def test_no_candles_a_year_back_is_too_new(self):
        from django.core.cache import cache

        cache.delete("universe:old_enough:C01:365")
        with mock.patch("apps.market_data.client.fetch_candle_window", return_value=[]):
            self.assertFalse(universe.old_enough("C01", 365, sleep=lambda s: None))

    def test_lookups_are_paced(self):
        from django.core.cache import cache

        cache.delete("universe:old_enough:C02:365")
        slept = []
        with mock.patch("apps.market_data.client.fetch_candle_window", return_value=[{}]):
            universe.old_enough("C02", 365, sleep=slept.append)
        self.assertEqual(slept, [universe.AGE_LOOKUP_PAUSE])
