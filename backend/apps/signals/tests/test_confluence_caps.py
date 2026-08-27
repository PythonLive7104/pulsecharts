"""Unit tests for the delivery-side correlated-exposure caps.

`cap_crypto_direction` only reads `.symbol.asset_class` and `.direction`, so these
run against light stand-ins rather than the DB — the logic under test is the
counting, not the ORM.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from apps.signals.confluence import cap_crypto_direction


def _sig(direction, asset_class="crypto", ticker="BTC-USD"):
    return SimpleNamespace(
        direction=direction,
        symbol=SimpleNamespace(asset_class=asset_class, ticker=ticker),
    )


class CapCryptoDirectionTests(SimpleTestCase):
    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=0)
    def test_zero_disables(self):
        reps = [_sig("BUY") for _ in range(50)]
        self.assertEqual(len(cap_crypto_direction(reps)), 50)

    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=3)
    def test_caps_per_direction(self):
        reps = [_sig("SELL") for _ in range(10)]
        self.assertEqual(len(cap_crypto_direction(reps)), 3)

    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=3)
    def test_directions_counted_separately(self):
        # A cap on correlated exposure must not make BUYs and SELLs compete: they are
        # opposite bets, so they diversify each other rather than stacking.
        reps = [_sig("BUY") for _ in range(5)] + [_sig("SELL") for _ in range(5)]
        kept = cap_crypto_direction(reps)
        self.assertEqual(sum(1 for s in kept if s.direction == "BUY"), 3)
        self.assertEqual(sum(1 for s in kept if s.direction == "SELL"), 3)

    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=2)
    def test_already_open_consumes_slots(self):
        # The whole point of counting across scans: four correlated calls arriving in
        # four consecutive scans are the same bet as four arriving together.
        already = [_sig("SELL"), _sig("SELL")]
        reps = [_sig("SELL") for _ in range(4)]
        self.assertEqual(cap_crypto_direction(reps, already_open=already), [])

    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=1)
    def test_forex_is_untouched(self):
        # Forex has its own, finer cap (cap_currency_exposure); this one must not
        # double-charge it.
        reps = [_sig("SELL", "forex", "EUR-USD") for _ in range(5)]
        self.assertEqual(len(cap_crypto_direction(reps)), 5)

    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=2)
    def test_open_forex_does_not_consume_crypto_slots(self):
        already = [_sig("SELL", "forex", "EUR-USD") for _ in range(5)]
        reps = [_sig("SELL") for _ in range(3)]
        self.assertEqual(len(cap_crypto_direction(reps, already_open=already)), 2)

    @override_settings(SIGNAL_MAX_CRYPTO_PER_DIRECTION=2)
    def test_order_preserved_first_claims_slot(self):
        # Callers control priority by ordering (feed newest-first, Telegram
        # oldest-first), so the cap must never reorder.
        a, b, c = _sig("BUY"), _sig("BUY"), _sig("BUY")
        self.assertEqual(cap_crypto_direction([a, b, c]), [a, b])


class FreshEntryQTests(SimpleTestCase):
    """`fresh_entry_q` builds the filter; these assert the Q's SHAPE rather than
    hitting the DB, since the guards are pure configuration logic."""

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=0)
    def test_both_off_is_empty_q(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        # An empty Q must match everything, so callers can AND it in unconditionally.
        self.assertEqual(len(fresh_entry_q(timezone.now())), 0)

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=True, SIGNAL_MAX_DELIVERY_AGE_BARS=0)
    def test_progressed_guard_filters_best_tp(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        self.assertIn(("best_tp", 0), fresh_entry_q(timezone.now()).children)

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=4)
    def test_age_guard_scales_with_timeframe(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        now = timezone.now()
        q = fresh_entry_q(now)
        # Flatten to (timeframe, cutoff) pairs and confirm 4h's window is 4x 1h's.
        cutoffs = {}
        for child in q.children:
            pairs = dict(getattr(child, "children", []) or [])
            tf = pairs.get("timeframe")
            if tf:
                cutoffs[tf] = pairs["generated_at__gte"]
        self.assertAlmostEqual(
            (now - cutoffs["1h"]).total_seconds(), 4 * 3600, delta=2)
        self.assertAlmostEqual(
            (now - cutoffs["4h"]).total_seconds(), 4 * 14400, delta=2)

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=4)
    def test_unknown_timeframe_is_not_dropped(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        # An unmapped frame gets no cutoff rather than vanishing from the feed.
        rendered = str(fresh_entry_q(timezone.now()))
        self.assertIn("NOT", rendered.upper())
