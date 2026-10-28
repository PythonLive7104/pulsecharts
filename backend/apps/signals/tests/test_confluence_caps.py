"""Unit tests for the delivery-side correlated-exposure caps.

`cap_crypto_direction` only reads `.symbol.asset_class` and `.direction`, so these
run against light stand-ins rather than the DB — the logic under test is the
counting, not the ORM.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from apps.signals.confluence import cap_crypto_direction, cap_per_symbol


def _sym_sig(symbol_id, timeframe="1h"):
    """Stand-in for cap_per_symbol, which only reads `.symbol_id`."""
    return SimpleNamespace(symbol_id=symbol_id, timeframe=timeframe)


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

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=0,
                       SIGNAL_MAX_ENTRY_DRIFT=0)
    def test_both_off_is_empty_q(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        # An empty Q must match everything, so callers can AND it in unconditionally.
        self.assertEqual(len(fresh_entry_q(timezone.now())), 0)

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=0,
                       SIGNAL_MAX_ENTRY_DRIFT=0.35)
    def test_entry_drift_guard_is_on_by_default(self):
        """The adverse-move guard belongs in the default filter.

        It defaulted to 0 while .env.example recommended 0.35, so an unconfigured
        deployment delivered signals whose price had already run most of the way to
        the stop at the entry still printed on the card.
        """
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q
        from django.conf import settings

        self.assertEqual(settings.SIGNAL_MAX_ENTRY_DRIFT, 0.35)
        q = fresh_entry_q(timezone.now())
        self.assertIn("mae_pct", str(q))

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=True, SIGNAL_MAX_DELIVERY_AGE_BARS=0,
                       SIGNAL_MAX_ENTRY_DRIFT=0)
    def test_progressed_guard_filters_best_tp(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        self.assertIn(("best_tp", 0), fresh_entry_q(timezone.now()).children)

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=4,
                       SIGNAL_MAX_ENTRY_DRIFT=0)
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

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=4,
                       SIGNAL_MAX_ENTRY_DRIFT=0)
    def test_unknown_timeframe_is_not_dropped(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        # An unmapped frame gets no cutoff rather than vanishing from the feed.
        rendered = str(fresh_entry_q(timezone.now()))
        self.assertIn("NOT", rendered.upper())


class EntryDriftTests(SimpleTestCase):
    """The adverse mirror of SIGNAL_SUPPRESS_PROGRESSED.

    mae_pct is NEGATIVE and risk_pct POSITIVE, so the comparison is easy to invert;
    these pin the direction down.
    """

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=0,
                       SIGNAL_MAX_ENTRY_DRIFT=0)
    def test_zero_disables(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        self.assertEqual(len(fresh_entry_q(timezone.now())), 0)

    @override_settings(SIGNAL_SUPPRESS_PROGRESSED=False, SIGNAL_MAX_DELIVERY_AGE_BARS=0,
                       SIGNAL_MAX_ENTRY_DRIFT=0.35)
    def test_builds_a_drift_clause(self):
        from django.utils import timezone

        from apps.signals.confluence import fresh_entry_q

        rendered = str(fresh_entry_q(timezone.now()))
        self.assertIn("mae_pct", rendered)
        # Null mae (evaluator has not run yet) must be KEPT, not dropped — a brand-new
        # signal is the freshest thing in the feed.
        self.assertIn("mae_pct__isnull", rendered)


class CapPerSymbolTests(SimpleTestCase):
    """One open call per coin.

    The scan generates per (symbol, strategy, timeframe) and `collapse` only reduces
    that to one per (symbol, timeframe), so a coin scanned on two timeframes — or one
    that re-qualifies after the re-entry cooldown while its first call is still
    running — produced several live cards for the same setup. This cap is what holds a
    symbol to one open trade until that trade actually resolves.
    """

    @override_settings(SIGNAL_MAX_OPEN_PER_SYMBOL=0)
    def test_zero_disables(self):
        reps = [_sym_sig(1) for _ in range(10)]
        self.assertEqual(len(cap_per_symbol(reps)), 10)

    @override_settings(SIGNAL_MAX_OPEN_PER_SYMBOL=1)
    def test_one_per_symbol_within_a_batch(self):
        # Same coin on 1h and 4h is one setup, not two.
        reps = [_sym_sig(1), _sym_sig(1), _sym_sig(2)]
        self.assertEqual([s.symbol_id for s in cap_per_symbol(reps)], [1, 2])

    @override_settings(SIGNAL_MAX_OPEN_PER_SYMBOL=1)
    def test_an_open_trade_blocks_the_next_call_on_that_coin(self):
        # Counted across scans — this is the "no new signal until TP/SL/invalidation"
        # rule, since a resolved signal is no longer in `already_open`.
        kept = cap_per_symbol([_sym_sig(1), _sym_sig(2)], already_open=[_sym_sig(1)])
        self.assertEqual([s.symbol_id for s in kept], [2])

    @override_settings(SIGNAL_MAX_OPEN_PER_SYMBOL=1)
    def test_order_decides_which_call_claims_the_slot(self):
        # Callers control priority (feed newest-first, Telegram oldest-first), so the
        # cap must never reorder.
        first, second = _sym_sig(1, timeframe="4h"), _sym_sig(1, timeframe="1h")
        self.assertEqual(cap_per_symbol([first, second]), [first])

    @override_settings(SIGNAL_MAX_OPEN_PER_SYMBOL=2)
    def test_a_higher_cap_allows_that_many(self):
        reps = [_sym_sig(1) for _ in range(5)]
        self.assertEqual(len(cap_per_symbol(reps)), 2)


class OpenPositionsChannelTests(TestCase):
    """`open_positions_for` must be scoped to ONE channel.

    Regression: it originally counted both channels at once. Web push records an
    in-app SignalDelivery for everything it sends, so the web push claimed the symbol
    seconds before the Telegram task ran and `cap_per_symbol` then suppressed the
    Telegram message for the SAME trade — a browser notification arrived and Telegram
    stayed silent.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from apps.market_data.models import Symbol
        from apps.signals.models import Signal, SignalService

        self.user = get_user_model().objects.create_user(
            email="trader@example.com", password="x"
        )
        symbol = Symbol.objects.create(ticker="ETHFI-USD", hl_coin="ETHFI")
        service = SignalService.objects.create(
            name="Volatility Breakout", slug="volatility-breakout"
        )
        self.signal = Signal.objects.create(
            symbol=symbol, service=service, direction=Signal.Direction.SELL,
            confidence_pct=80, timeframe="1h", entry_price=0.654, stop_loss=0.67,
            tp1=0.638, tp2=0.622, tp3=0.606,
            risk_pct=2.4, reward_tp1_pct=2.4, reward_tp2_pct=4.8, reward_tp3_pct=7.2,
            risk_reward_tp1=1.0, risk_reward_tp2=2.0, risk_reward_tp3=3.0,
            dollar_risk=2.4, dollar_tp1=2.4, dollar_tp2=4.8, dollar_tp3=7.2,
            generated_at=timezone.now(),
        )

    def test_an_in_app_delivery_does_not_count_as_a_telegram_position(self):
        from apps.signals.confluence import open_positions_for
        from apps.signals.models import SignalDelivery

        SignalDelivery.objects.create(user=self.user, signal=self.signal)

        self.assertEqual(list(open_positions_for(self.user, "feed")), [self.signal])
        # The whole point: Telegram has sent nothing, so nothing blocks it.
        self.assertEqual(list(open_positions_for(self.user, "telegram")), [])

    def test_a_telegram_delivery_does_not_count_as_a_feed_position(self):
        from apps.signals.confluence import open_positions_for
        from apps.signals.models import TelegramDelivery

        TelegramDelivery.objects.create(user=self.user, signal=self.signal)

        self.assertEqual(list(open_positions_for(self.user, "telegram")), [self.signal])
        self.assertEqual(list(open_positions_for(self.user, "feed")), [])

    def test_resolved_signals_free_the_symbol_again(self):
        from apps.signals.confluence import open_positions_for
        from apps.signals.models import Signal, TelegramDelivery

        TelegramDelivery.objects.create(user=self.user, signal=self.signal)
        self.signal.outcome = Signal.Outcome.TP1
        self.signal.save(update_fields=["outcome"])

        self.assertEqual(list(open_positions_for(self.user, "telegram")), [])
