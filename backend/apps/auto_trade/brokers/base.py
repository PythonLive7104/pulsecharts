"""Broker client interface.

The execution engine talks to exchanges only through this interface, so adding a
second exchange later (or swapping pybit for ccxt) doesn't touch the engine. Only
the verification/balance methods are implemented in Phase 1; order placement is
Phase 2 and is declared here so the contract is visible up front.
"""

from __future__ import annotations

from dataclasses import dataclass


class BrokerError(Exception):
    """Any broker-side failure (auth, network, rejected order)."""


@dataclass
class VerifyResult:
    """Outcome of checking a set of API credentials."""

    ok: bool                     # credentials authenticate and can trade
    can_trade: bool              # key has derivatives/contract trade permission
    can_withdraw: bool           # key has withdrawal permission (MUST be False)
    message: str = ""            # human-readable reason when ok is False

    @property
    def safe(self) -> bool:
        """Usable for auto-trade: can place trades but CANNOT withdraw funds."""
        return self.ok and self.can_trade and not self.can_withdraw


class BrokerClient:
    """Abstract broker client. Concrete subclasses wrap a specific exchange SDK."""

    def verify(self) -> VerifyResult:
        """Authenticate and report trade/withdraw permissions."""
        raise NotImplementedError

    def get_equity_usd(self) -> float:
        """Total account equity in USD(T), used for risk-% position sizing."""
        raise NotImplementedError

    # --- Phase 2 (order placement) — declared, not yet implemented ---

    def get_last_price(self, symbol: str) -> float:
        raise NotImplementedError

    def place_market_entry(self, symbol, side, qty, leverage):
        raise NotImplementedError

    def place_protective_orders(self, symbol, side, qty, stop_loss, take_profits):
        raise NotImplementedError

    def close_position(self, symbol):
        raise NotImplementedError

    # --- Phase 3 (reconciliation) ---

    def get_position(self, symbol):
        """Current open position for a symbol, or None if flat. Shape:
        {"size": float, "side": "Buy"|"Sell", "avg_price": float}."""
        raise NotImplementedError

    def get_realized_pnl(self, symbol, since_ms):
        """Summed realized PnL (USD) for the symbol since `since_ms` (epoch ms)."""
        raise NotImplementedError

    def amend_stop_trigger(self, symbol, order_id, new_trigger):
        """Move an existing stop order's trigger price (used for break-even)."""
        raise NotImplementedError

    def cancel_all_orders(self, symbol):
        """Cancel all remaining (protective) orders for a symbol."""
        raise NotImplementedError
