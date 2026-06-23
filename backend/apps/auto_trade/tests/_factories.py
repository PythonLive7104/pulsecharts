"""Test fixtures: object builders + an in-memory fake broker client.

Not named test*, so the runner won't collect it as a test module.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.auto_trade.brokers import BrokerError
from apps.auto_trade.models import AutoTradeConfig, BrokerConnection
from apps.market_data.models import Symbol
from apps.signals.models import Signal, SignalService, UserSignalSubscription
from apps.watchlists.models import WatchlistItem

User = get_user_model()


def make_pro_user(email="pro@example.com"):
    """A user on an active (unexpired) Pro plan."""
    return User.objects.create_user(
        email=email, password="x",
        plan_tier="pro", plan_expiry=timezone.now() + timedelta(days=30),
    )


def make_symbol(ticker="BTC-USD", bybit_symbol="BTCUSDT"):
    return Symbol.objects.create(ticker=ticker, hl_coin="BTC", bybit_symbol=bybit_symbol)


def make_service(slug="momentum"):
    return SignalService.objects.create(name="Momentum", slug=slug)


def make_broker(user, **over):
    """A verified, usable broker connection. Credentials are dummy ciphertext —
    encryption isn't exercised here (the fake client is injected)."""
    defaults = dict(
        api_key_enc="enc", api_secret_enc="enc",
        status=BrokerConnection.Status.ACTIVE, permission_verified=True,
        testnet=True,
    )
    defaults.update(over)
    return BrokerConnection.objects.create(user=user, **defaults)


def make_config(user, **over):
    defaults = dict(enabled=True, tp_distribution=[25, 25, 25, 25])
    defaults.update(over)
    return AutoTradeConfig.objects.create(user=user, **defaults)


def make_signal(symbol, service, *, direction="BUY", confidence=80,
                entry=100.0, stop=95.0, **over):
    """A pending BUY signal with self-consistent TP/risk fields."""
    risk = abs(entry - stop)
    sign = 1 if direction == "BUY" else -1
    defaults = dict(
        symbol=symbol, service=service, direction=direction,
        confidence_pct=confidence, timeframe="1h", generated_at=timezone.now(),
        entry_price=entry, stop_loss=stop,
        tp1=entry + sign * risk, tp2=entry + sign * 2 * risk,
        tp3=entry + sign * 3 * risk, tp4=entry + sign * 4.5 * risk,
        risk_pct=risk / entry * 100,
        reward_tp1_pct=1.0, reward_tp2_pct=2.0, reward_tp3_pct=3.0, reward_tp4_pct=4.5,
        risk_reward_tp1=1.0, risk_reward_tp2=2.0, risk_reward_tp3=3.0, risk_reward_tp4=4.5,
        dollar_risk=5.0, dollar_tp1=5.0, dollar_tp2=10.0, dollar_tp3=15.0, dollar_tp4=22.5,
        outcome=Signal.Outcome.PENDING,
    )
    defaults.update(over)
    return Signal.objects.create(**defaults)


def subscribe_and_watch(user, signal):
    """Make `user` eligible for `signal`: follow its strategy + watch its symbol."""
    UserSignalSubscription.objects.create(user=user, service=signal.service)
    WatchlistItem.objects.create(user=user, symbol=signal.symbol)


class FakeBroker:
    """In-memory broker client. Records calls and returns scriptable responses."""

    def __init__(self, *, last_price=100.0, equity=10_000.0, fill_price=100.0,
                 position=None, realized=12.5, protective_raises=False,
                 close_raises=False):
        self.last_price = last_price
        self.equity = equity
        self.fill_price = fill_price
        self.position = position          # dict or None (None = flat)
        self.realized = realized
        self.protective_raises = protective_raises
        self.close_raises = close_raises
        self.calls = []

    # --- read ---
    def get_last_price(self, symbol):
        self.calls.append(("price", symbol))
        return self.last_price

    def get_equity_usd(self):
        return self.equity

    def get_position(self, symbol):
        self.calls.append(("position", symbol))
        return self.position

    def get_realized_pnl(self, symbol, since_ms):
        return self.realized

    # --- write ---
    def place_market_entry(self, symbol, side, qty, leverage):
        self.calls.append(("entry", symbol, side, qty, leverage))
        return {"order_id": "E1", "qty": qty, "avg_price": self.fill_price}

    def place_protective_orders(self, symbol, side, qty, stop_loss, take_profits):
        if self.protective_raises:
            raise BrokerError("protection rejected")
        self.calls.append(("protect", symbol, side, qty, stop_loss, take_profits))
        return {"sl": "S1", "tp": ["T1", "T2", "T3", "T4"]}

    def close_position(self, symbol):
        self.calls.append(("close", symbol))
        if self.close_raises:
            raise BrokerError("close rejected")
        return {"closed": True}

    def cancel_all_orders(self, symbol):
        self.calls.append(("cancel", symbol))
        return {"cancelled": True}

    def amend_stop_trigger(self, symbol, order_id, new_trigger):
        self.calls.append(("amend", symbol, order_id, new_trigger))
        return True

    def has_call(self, name):
        return any(c[0] == name for c in self.calls)
