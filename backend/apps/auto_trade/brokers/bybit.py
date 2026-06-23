"""Bybit V5 client (official `pybit` SDK, unified trading account).

Trades USDT perpetuals (category "linear"), one-way position mode (positionIdx 0).
Entry is a market order; protection is a reduce-only stop-market for the SL plus
one reduce-only limit per take-profit (scaled out), so the four-TP card maps to
four real orders. Quantities/prices are rounded to each instrument's qtyStep /
tickSize before submission.

NOTE: Bybit's V5 API shape (permission keys, balance fields, order params) has
shifted across versions. Parsing here is defensive, but re-verify field names and
order behaviour against the live Bybit docs / current pybit on a TESTNET key
before going anywhere near real funds.
"""

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .base import BrokerClient, BrokerError, VerifyResult

logger = logging.getLogger("auto_trade.bybit")

_CATEGORY = "linear"  # USDT-margined perpetuals


def _round_down(value: float, step: float) -> float:
    """Round a quantity DOWN to the instrument's lot step (never over-size)."""
    if not step:
        return value
    d = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(ROUND_DOWN)
    return float(d * Decimal(str(step)))


def _round_price(value: float, tick: float) -> float:
    """Round a price to the nearest tick."""
    if not tick:
        return value
    d = (Decimal(str(value)) / Decimal(str(tick))).to_integral_value(ROUND_HALF_UP)
    return float(d * Decimal(str(tick)))


class BybitClient(BrokerClient):
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        try:
            from pybit.unified_trading import HTTP
        except ImportError as exc:  # pragma: no cover
            raise BrokerError("pybit is not installed") from exc
        self._session = HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)
        self._filters: dict[str, dict] = {}  # symbol -> {qty_step, min_qty, tick_size}

    def verify(self) -> VerifyResult:
        try:
            resp = self._session.get_api_key_information()
        except Exception as exc:  # pybit raises various exception types
            return VerifyResult(ok=False, can_trade=False, can_withdraw=False,
                                message=f"authentication failed: {exc}")

        result = (resp or {}).get("result") or {}
        perms = result.get("permissions") or {}

        # Withdrawal lives under the Wallet permission group. ANY withdraw-capable
        # scope makes the key unsafe for us to hold.
        wallet = [p.lower() for p in (perms.get("Wallet") or [])]
        can_withdraw = any("withdraw" in p for p in wallet)

        # Derivatives/contract trade permission — accept either group name Bybit
        # has used, and require an order-capable scope.
        trade_groups = (perms.get("ContractTrade") or []) + (perms.get("Derivatives") or [])
        trade_scopes = [p.lower() for p in trade_groups]
        can_trade = any("order" in p or "position" in p for p in trade_scopes)

        if can_withdraw:
            return VerifyResult(
                ok=False, can_trade=can_trade, can_withdraw=True,
                message=("This API key has withdrawal permission. For your safety, "
                         "create a key with Trade enabled and Withdraw DISABLED."),
            )
        if not can_trade:
            return VerifyResult(
                ok=False, can_trade=False, can_withdraw=False,
                message="This API key lacks derivatives/contract trade permission.",
            )
        return VerifyResult(ok=True, can_trade=True, can_withdraw=False)

    def get_equity_usd(self) -> float:
        try:
            resp = self._session.get_wallet_balance(accountType="UNIFIED")
        except Exception as exc:
            raise BrokerError(f"could not read balance: {exc}") from exc
        rows = ((resp or {}).get("result") or {}).get("list") or []
        if not rows:
            return 0.0
        # Unified account reports a single aggregated equity figure in USD.
        try:
            return float(rows[0].get("totalEquity") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # --- order placement -------------------------------------------------

    def get_instrument_filters(self, symbol: str) -> dict:
        """Lot/price filters for a symbol: {qty_step, min_qty, tick_size}. Cached."""
        if symbol in self._filters:
            return self._filters[symbol]
        try:
            resp = self._session.get_instruments_info(category=_CATEGORY, symbol=symbol)
        except Exception as exc:
            raise BrokerError(f"could not load instrument info for {symbol}: {exc}") from exc
        rows = ((resp or {}).get("result") or {}).get("list") or []
        if not rows:
            raise BrokerError(f"unknown Bybit instrument: {symbol}")
        row = rows[0]
        lot = row.get("lotSizeFilter") or {}
        price = row.get("priceFilter") or {}
        filters = {
            "qty_step": float(lot.get("qtyStep") or 0) or 0.0,
            "min_qty": float(lot.get("minOrderQty") or 0) or 0.0,
            "tick_size": float(price.get("tickSize") or 0) or 0.0,
        }
        self._filters[symbol] = filters
        return filters

    def get_last_price(self, symbol: str) -> float:
        try:
            resp = self._session.get_tickers(category=_CATEGORY, symbol=symbol)
        except Exception as exc:
            raise BrokerError(f"could not read ticker for {symbol}: {exc}") from exc
        rows = ((resp or {}).get("result") or {}).get("list") or []
        if not rows:
            raise BrokerError(f"no ticker for {symbol}")
        return float(rows[0].get("lastPrice") or 0.0)

    def round_qty(self, symbol: str, qty: float) -> float:
        return _round_down(qty, self.get_instrument_filters(symbol)["qty_step"])

    def _set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage; ignore the 'leverage not modified' error Bybit returns
        when it's already at the requested value."""
        try:
            self._session.set_leverage(
                category=_CATEGORY, symbol=symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage),
            )
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return
            raise BrokerError(f"could not set leverage on {symbol}: {exc}") from exc

    def place_market_entry(self, symbol: str, side: str, qty: float, leverage: int) -> dict:
        """Market-enter `qty` (base units) on `symbol`. side is 'Buy'/'Sell'.
        Returns {"order_id": str, "avg_price": float|None}."""
        self._set_leverage(symbol, leverage)
        qty = self.round_qty(symbol, qty)
        if qty <= 0:
            raise BrokerError(f"computed qty rounds to 0 for {symbol}")
        try:
            resp = self._session.place_order(
                category=_CATEGORY, symbol=symbol, side=side,
                orderType="Market", qty=str(qty), positionIdx=0,
            )
        except Exception as exc:
            raise BrokerError(f"entry order rejected for {symbol}: {exc}") from exc
        result = (resp or {}).get("result") or {}
        avg = result.get("avgPrice")
        return {
            "order_id": result.get("orderId", ""),
            "qty": qty,
            "avg_price": float(avg) if avg else None,
        }

    def place_protective_orders(
        self, symbol: str, side: str, qty: float, stop_loss: float, take_profits: list[tuple[float, float]]
    ) -> dict:
        """Place a reduce-only stop-market SL for the full qty plus one reduce-only
        limit per take-profit. `side` is the ENTRY side; protective orders are the
        opposite. `take_profits` is a list of (price, qty) tuples (qty already
        split per the TP distribution). Returns {"sl": id, "tp": [ids...]}."""
        exit_side = "Sell" if side == "Buy" else "Buy"
        filters = self.get_instrument_filters(symbol)
        tick = filters["tick_size"]
        # SL triggers when price moves against the position: a long's stop is below
        # (triggerDirection 2 = falling); a short's is above (1 = rising).
        trigger_direction = 2 if side == "Buy" else 1
        sl_price = _round_price(stop_loss, tick)

        order_ids: dict = {"sl": "", "tp": []}
        try:
            sl_resp = self._session.place_order(
                category=_CATEGORY, symbol=symbol, side=exit_side,
                orderType="Market", qty=str(self.round_qty(symbol, qty)),
                triggerPrice=str(sl_price), triggerDirection=trigger_direction,
                triggerBy="LastPrice", reduceOnly=True, positionIdx=0,
            )
            order_ids["sl"] = ((sl_resp or {}).get("result") or {}).get("orderId", "")
        except Exception as exc:
            raise BrokerError(f"stop-loss order rejected for {symbol}: {exc}") from exc

        for tp_price, tp_qty in take_profits:
            q = self.round_qty(symbol, tp_qty)
            if q <= 0:
                continue
            try:
                tp_resp = self._session.place_order(
                    category=_CATEGORY, symbol=symbol, side=exit_side,
                    orderType="Limit", qty=str(q), price=str(_round_price(tp_price, tick)),
                    reduceOnly=True, positionIdx=0,
                )
                order_ids["tp"].append(((tp_resp or {}).get("result") or {}).get("orderId", ""))
            except Exception as exc:
                # A TP rejection isn't fatal — the SL still protects the position.
                logger.warning("TP order rejected for %s @ %s: %s", symbol, tp_price, exc)
        return order_ids

    def close_position(self, symbol: str) -> dict:
        """Market-close the whole position (reduce-only). Used by the kill switch
        and on signal invalidation. No-op if there's no open position."""
        pos = self.get_position(symbol)
        if pos is None:
            return {"closed": False}
        size = pos["size"]
        exit_side = "Sell" if pos["side"] == "Buy" else "Buy"
        try:
            self._session.place_order(
                category=_CATEGORY, symbol=symbol, side=exit_side,
                orderType="Market", qty=str(size), reduceOnly=True, positionIdx=0,
            )
        except Exception as exc:
            raise BrokerError(f"close order rejected for {symbol}: {exc}") from exc
        return {"closed": True, "qty": size}

    # --- reconciliation (Phase 3) ----------------------------------------

    def get_position(self, symbol: str) -> dict | None:
        """Current open position, or None if flat. {"size", "side", "avg_price"}."""
        try:
            resp = self._session.get_positions(category=_CATEGORY, symbol=symbol)
        except Exception as exc:
            raise BrokerError(f"could not read position for {symbol}: {exc}") from exc
        rows = ((resp or {}).get("result") or {}).get("list") or []
        if not rows:
            return None
        size = float(rows[0].get("size") or 0.0)
        if size <= 0:
            return None
        try:
            avg = float(rows[0].get("avgPrice") or 0.0)
        except (TypeError, ValueError):
            avg = 0.0
        return {"size": size, "side": rows[0].get("side"), "avg_price": avg}

    def get_realized_pnl(self, symbol: str, since_ms: int) -> float:
        """Summed closed PnL (USD) for `symbol` recorded at/after `since_ms`."""
        try:
            resp = self._session.get_closed_pnl(
                category=_CATEGORY, symbol=symbol, limit=100
            )
        except Exception as exc:
            raise BrokerError(f"could not read closed pnl for {symbol}: {exc}") from exc
        rows = ((resp or {}).get("result") or {}).get("list") or []
        total = 0.0
        for row in rows:
            try:
                ts = int(row.get("updatedTime") or row.get("createdTime") or 0)
            except (TypeError, ValueError):
                ts = 0
            if ts and ts < since_ms:
                continue
            try:
                total += float(row.get("closedPnl") or 0.0)
            except (TypeError, ValueError):
                continue
        return total

    def amend_stop_trigger(self, symbol: str, order_id: str, new_trigger: float) -> bool:
        """Move a stop order's trigger price (break-even). Returns False if the
        order is gone (already filled/cancelled) — not an error for the caller."""
        if not order_id:
            return False
        tick = self.get_instrument_filters(symbol)["tick_size"]
        try:
            self._session.amend_order(
                category=_CATEGORY, symbol=symbol, orderId=order_id,
                triggerPrice=str(_round_price(new_trigger, tick)),
            )
            return True
        except Exception as exc:
            if "not exists" in str(exc).lower() or "not modified" in str(exc).lower():
                return False
            raise BrokerError(f"could not amend stop on {symbol}: {exc}") from exc

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all remaining orders for a symbol (leftover protective legs after
        a manual close). No-op if there's nothing to cancel."""
        try:
            self._session.cancel_all_orders(category=_CATEGORY, symbol=symbol)
        except Exception as exc:
            if "not exists" in str(exc).lower():
                return {"cancelled": False}
            raise BrokerError(f"could not cancel orders on {symbol}: {exc}") from exc
        return {"cancelled": True}
