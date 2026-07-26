"""Executor tests — risk gating, dedup, and placement, with Bybit fully mocked.

These exercise the orchestration in apps.execution.executor without any network:
the Bybit client is replaced by a fake that records what would have been placed.
"""

from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.execution import crypto
from apps.execution.bybit import Instrument
from apps.execution.models import BrokerCredential, TradeExecution
from apps.market_data.models import Symbol
from apps.signals.models import Signal, SignalService, UserSignalSubscription
from apps.watchlists.models import WatchlistItem

_KEY = Fernet.generate_key().decode()

User = get_user_model()


class FakeBybit:
    """Stand-in for BybitClient. Records placed orders; no network. Class-level logs
    are reset in setUp so assertions read one test's activity."""

    placed = []       # market entry brackets
    rungs = []        # reduce-only limit TP rungs
    stops = []        # set_stop_loss (breakeven) calls
    cancels = []      # cancel_order calls
    positions = []    # what get_positions returns
    min_order_qty = 0.001

    def __init__(self, *a, **k):
        pass

    def get_instrument(self, symbol):
        return Instrument(symbol=symbol, min_order_qty=FakeBybit.min_order_qty,
                          qty_step=0.001, min_notional=5.0, max_leverage=50)

    def get_last_price(self, symbol):
        return 100.0

    def set_leverage(self, symbol, leverage):
        pass

    def place_market_bracket(self, symbol, side, qty, stop_loss, take_profit, order_link_id):
        FakeBybit.placed.append(dict(symbol=symbol, side=side, qty=qty,
                                     stop_loss=stop_loss, take_profit=take_profit,
                                     order_link_id=order_link_id))
        return "ORD-" + order_link_id

    def place_reduce_limit(self, symbol, side, qty, price, order_link_id):
        FakeBybit.rungs.append(dict(symbol=symbol, side=side, qty=qty, price=price,
                                    order_link_id=order_link_id))
        return "RID-" + order_link_id

    def set_stop_loss(self, symbol, stop_loss):
        FakeBybit.stops.append(dict(symbol=symbol, stop_loss=stop_loss))

    def cancel_order(self, symbol, order_link_id):
        FakeBybit.cancels.append(order_link_id)
        return True

    def get_positions(self, symbol=None):
        return FakeBybit.positions


@override_settings(
    BROKER_ENCRYPTION_KEY=_KEY,
    AUTO_TRADE_ENABLED=True,
    SIGNAL_CONFLUENCE_MIN=1,   # a single strategy suffices to surface in the test
    SIGNAL_MIN_CONFIDENCE=65,
    AUTO_TRADE_MAX_SIGNAL_AGE_SEC=3600,
    AUTO_TRADE_MAX_SLIPPAGE_PCT=0.5,
)
class ExecutorTests(TestCase):
    def setUp(self):
        crypto._fernet.cache_clear()
        FakeBybit.placed = []
        FakeBybit.rungs = []
        FakeBybit.stops = []
        FakeBybit.cancels = []
        FakeBybit.positions = []
        FakeBybit.min_order_qty = 0.001
        self.user = User.objects.create_user("t@example.com", "pw",
                                              plan_tier="pro",
                                              plan_expiry=timezone.now() + timedelta(days=30))
        self.symbol = Symbol.objects.create(ticker="BTC-USD", hl_coin="BTC",
                                            asset_class=Symbol.AssetClass.CRYPTO)
        self.service = SignalService.objects.create(name="Momentum", slug="momentum")
        UserSignalSubscription.objects.create(user=self.user, service=self.service)
        WatchlistItem.objects.create(user=self.user, symbol=self.symbol, sort_order=0)
        self.cred = BrokerCredential(user=self.user, enabled=True, testnet=True,
                                     margin_per_trade_usd=1.0, max_leverage=10)
        self.cred.set_api_key("key")
        self.cred.set_api_secret("secret")
        self.cred.save()

    def tearDown(self):
        crypto._fernet.cache_clear()

    def _make_signal(self, **over):
        defaults = dict(
            symbol=self.symbol, service=self.service, direction=Signal.Direction.BUY,
            confidence_pct=80, timeframe="1h", generated_at=timezone.now(),
            entry_price=100.0, stop_loss=97.0, tp1=103.0, tp2=106.0, tp3=109.0,
            risk_pct=3.0, reward_tp1_pct=3.0, reward_tp2_pct=6.0, reward_tp3_pct=9.0,
            risk_reward_tp1=1.0, risk_reward_tp2=2.0, risk_reward_tp3=3.0,
            dollar_risk=3.0, dollar_tp1=3.0, dollar_tp2=6.0, dollar_tp3=9.0,
        )
        defaults.update(over)
        return Signal.objects.create(**defaults)

    def _run(self):
        from apps.execution import executor
        with mock.patch.object(executor, "BybitClient", FakeBybit):
            return executor.run_auto_trades()

    def test_places_scaleout_ladder(self):
        # Default AUTO_TRADE_SCALEOUT=True: entry carries the stop but NO tp, and three
        # reduce-only rungs are laid at TP1/TP2/TP3 with 50/25/25 sizes.
        self._make_signal()
        result = self._run()
        self.assertEqual(result["placed"], 1)
        self.assertEqual(len(FakeBybit.placed), 1)
        entry = FakeBybit.placed[0]
        self.assertIsNone(entry["take_profit"])  # laddered, not a single bracket TP
        self.assertEqual(entry["stop_loss"], 97.0)

        self.assertEqual(len(FakeBybit.rungs), 3)
        prices = sorted(r["price"] for r in FakeBybit.rungs)
        self.assertEqual(prices, [103.0, 106.0, 109.0])  # tp1/tp2/tp3
        self.assertTrue(all(r["side"] == "Sell" for r in FakeBybit.rungs))  # closing a long
        # 50/25/25 split of the total qty across the rungs.
        rung_qty = sum(r["qty"] for r in FakeBybit.rungs)
        ex = TradeExecution.objects.get(user=self.user)
        self.assertTrue(ex.scaleout)
        self.assertEqual(ex.take_profit, 103.0)  # first target recorded for the card
        self.assertAlmostEqual(rung_qty, ex.qty, places=6)  # rungs cover the whole size

    @override_settings(AUTO_TRADE_SCALEOUT=False)
    def test_single_tp_fallback_when_scaleout_off(self):
        self._make_signal()
        result = self._run()
        self.assertEqual(result["placed"], 1)
        self.assertEqual(len(FakeBybit.rungs), 0)  # no ladder
        entry = FakeBybit.placed[0]
        self.assertEqual(entry["take_profit"], 106.0)  # single tp2 bracket
        ex = TradeExecution.objects.get(user=self.user)
        self.assertFalse(ex.scaleout)

    def test_tiny_position_falls_back_to_single_tp(self):
        # A large min-order-qty makes every 50/25/25 tranche sub-minimum → single TP.
        FakeBybit.min_order_qty = 0.05
        self._make_signal()
        result = self._run()
        self.assertEqual(result["placed"], 1)
        self.assertEqual(len(FakeBybit.rungs), 0)
        ex = TradeExecution.objects.get(user=self.user)
        self.assertFalse(ex.scaleout)
        self.assertEqual(FakeBybit.placed[0]["take_profit"], 106.0)

    def test_dedup_no_second_order_for_same_signal(self):
        self._make_signal()
        self._run()
        self._run()  # second pass must not re-place
        self.assertEqual(TradeExecution.objects.filter(user=self.user).count(), 1)
        self.assertEqual(len(FakeBybit.placed), 1)

    def test_feature_flag_off_is_noop(self):
        self._make_signal()
        with override_settings(AUTO_TRADE_ENABLED=False):
            result = self._run()
        self.assertIn("skipped", result)
        self.assertFalse(TradeExecution.objects.exists())

    def test_non_premium_user_skipped(self):
        self.user.plan_tier = "free"
        self.user.plan_expiry = None
        self.user.save()
        self._make_signal()
        result = self._run()
        self.assertEqual(result.get("placed", 0), 0)
        self.assertFalse(TradeExecution.objects.exists())

    def test_disabled_credential_skipped(self):
        self.cred.enabled = False
        self.cred.save()
        self._make_signal()
        result = self._run()
        self.assertEqual(result.get("users", 0), 0)
        self.assertFalse(TradeExecution.objects.exists())

    def test_forex_signal_not_traded(self):
        fx = Symbol.objects.create(ticker="EUR-USD", hl_coin="", feed_symbol="EURUSD=X",
                                   asset_class=Symbol.AssetClass.FOREX)
        WatchlistItem.objects.create(user=self.user, symbol=fx, sort_order=1)
        self._make_signal(symbol=fx)
        result = self._run()
        self.assertEqual(result.get("placed", 0), 0)

    def test_max_open_positions_respected(self):
        self.cred.max_open_positions = 0  # no room
        self.cred.save()
        self._make_signal()
        result = self._run()
        self.assertEqual(result.get("placed", 0), 0)
        self.assertFalse(TradeExecution.objects.exists())

    def test_slippage_guard_skips(self):
        # Signal entry 100 but live price is 100 in the fake; force a wide gap by
        # setting the signal entry far from the fake's 100 last price.
        self._make_signal(entry_price=90.0, stop_loss=87.0)
        result = self._run()
        self.assertEqual(result.get("placed", 0), 0)
        ex = TradeExecution.objects.get(user=self.user)
        self.assertEqual(ex.status, TradeExecution.Status.SKIPPED)
        self.assertIn("slippage", ex.reason)

    def test_second_signal_same_symbol_not_netted(self):
        # Two deliverable signals on the same coin (different timeframes) must not open
        # two positions that Bybit would net together.
        self._make_signal(timeframe="1h")
        self._make_signal(timeframe="4h")
        result = self._run()
        self.assertEqual(result["placed"], 1)
        self.assertEqual(TradeExecution.objects.filter(
            user=self.user, status=TradeExecution.Status.OPEN).count(), 1)

    # --- reconcile ---------------------------------------------------------
    def _open_execution(self, **over):
        defaults = dict(
            user=self.user, bybit_symbol="BTCUSDT", direction=Signal.Direction.BUY,
            status=TradeExecution.Status.OPEN, scaleout=True, qty=0.05,
            entry_price=100.0, stop_loss=97.0, liq_price=90.0,
            order_link_id="pc-99-{}".format(self.user.id),
        )
        defaults.update(over)
        return TradeExecution.objects.create(**defaults)

    def _reconcile(self):
        from apps.execution import executor
        with mock.patch.object(executor, "BybitClient", FakeBybit):
            return executor.run_reconcile()

    def test_reconcile_moves_stop_to_breakeven_when_position_shrinks(self):
        row = self._open_execution()
        FakeBybit.positions = [{"symbol": "BTCUSDT", "size": "0.02"}]  # a TP rung filled
        result = self._reconcile()
        self.assertEqual(result["breakeven_moved"], 1)
        self.assertEqual(len(FakeBybit.stops), 1)
        self.assertEqual(FakeBybit.stops[0]["stop_loss"], 100.0)  # trailed to entry
        row.refresh_from_db()
        self.assertTrue(row.breakeven_moved)
        self.assertEqual(row.status, TradeExecution.Status.OPEN)  # still running

    def test_reconcile_breakeven_only_once(self):
        row = self._open_execution(breakeven_moved=True)
        FakeBybit.positions = [{"symbol": "BTCUSDT", "size": "0.02"}]
        result = self._reconcile()
        self.assertEqual(result["breakeven_moved"], 0)
        self.assertEqual(len(FakeBybit.stops), 0)

    def test_reconcile_closes_and_cancels_rungs_when_flat(self):
        row = self._open_execution()
        FakeBybit.positions = []  # position gone
        result = self._reconcile()
        self.assertEqual(result["closed"], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, TradeExecution.Status.CLOSED)
        self.assertIsNotNone(row.closed_at)
        base = row.order_link_id
        self.assertEqual(
            set(FakeBybit.cancels),
            {f"{base}-tp1", f"{base}-tp2", f"{base}-tp3"},
        )

    def test_reconcile_infers_liquidation(self):
        # last price (100 in the fake) at/through the liq price ⇒ LIQUIDATED, not CLOSED.
        row = self._open_execution(liq_price=101.0)
        FakeBybit.positions = []
        result = self._reconcile()
        self.assertEqual(result["liquidated"], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, TradeExecution.Status.LIQUIDATED)
