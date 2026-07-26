"""Thin Bybit v5 REST client (no SDK — just `requests` + HMAC, like the rest of
the app talks to Telegram/Hyperliquid/Yahoo).

Covers only what the executor needs on USDT perpetuals (``category=linear``):
instrument filters, last price, wallet balance, set-leverage, place a market
bracket order (entry + stop-loss + take-profit), and read open positions.

Signing (v5): sign ``timestamp + api_key + recv_window + payload`` with HMAC-SHA256,
where ``payload`` is the query string for GET and the raw JSON body for POST. Public
market endpoints are unsigned. Docs: https://bybit-exchange.github.io/docs/v5/intro

Every method raises ``BybitError`` on a transport failure or a non-zero Bybit
``retCode`` so the executor can record the reason per user and move on. Nothing here
imports Django — the client is constructed with plain credentials, keeping it unit-
testable with a mocked ``requests``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import requests

MAINNET = "https://api.bybit.com"
TESTNET = "https://api-testnet.bybit.com"
RECV_WINDOW = "5000"  # ms the request stays valid server-side


class BybitError(RuntimeError):
    """Transport failure or a non-zero Bybit retCode. ``code`` is the Bybit retCode
    when available (None for pure transport errors)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class Instrument:
    """The subset of instruments-info the sizer needs."""

    symbol: str
    min_order_qty: float
    qty_step: float
    min_notional: float
    max_leverage: int


class BybitClient:
    def __init__(self, api_key: str = "", api_secret: str = "", *, testnet: bool = True,
                 timeout: float = 10.0):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = TESTNET if testnet else MAINNET
        self.timeout = timeout

    # --- low-level request plumbing -------------------------------------------
    def _sign(self, ts: str, payload: str) -> str:
        origin = f"{ts}{self.api_key}{RECV_WINDOW}{payload}"
        return hmac.new(
            self.api_secret.encode(), origin.encode(), hashlib.sha256
        ).hexdigest()

    def _headers(self, ts: str, payload: str, *, signed: bool) -> dict:
        if not signed:
            return {}
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": self._sign(ts, payload),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: dict | None = None,
                 *, signed: bool = False):
        params = params or {}
        ts = str(int(time.time() * 1000))
        url = self.base + path
        try:
            if method == "GET":
                query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                resp = requests.get(
                    url, params=params, headers=self._headers(ts, query, signed=signed),
                    timeout=self.timeout,
                )
            else:
                body = json.dumps(params, separators=(",", ":")) if params else ""
                resp = requests.post(
                    url, data=body, headers=self._headers(ts, body, signed=signed),
                    timeout=self.timeout,
                )
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise BybitError(f"bybit {method} {path} transport error: {exc}") from exc

        ret_code = data.get("retCode")
        if ret_code not in (0, None):
            raise BybitError(
                f"bybit {path} rejected: {data.get('retMsg', 'unknown')} (retCode={ret_code})",
                code=ret_code,
            )
        return data.get("result", {})

    # --- public market data (unsigned) ----------------------------------------
    def get_instrument(self, symbol: str) -> Instrument:
        result = self._request(
            "GET", "/v5/market/instruments-info",
            {"category": "linear", "symbol": symbol},
        )
        rows = result.get("list") or []
        if not rows:
            raise BybitError(f"instrument not found on Bybit: {symbol}")
        row = rows[0]
        lot = row.get("lotSizeFilter", {})
        lev = row.get("leverageFilter", {})
        # minNotionalValue is present on most linear perps; fall back to Bybit's
        # documented 5-USDT floor when a symbol omits it.
        min_notional = float(lot.get("minNotionalValue") or 5.0)
        return Instrument(
            symbol=symbol,
            min_order_qty=float(lot.get("minOrderQty", 0) or 0),
            qty_step=float(lot.get("qtyStep", 0) or 0),
            min_notional=min_notional,
            max_leverage=int(float(lev.get("maxLeverage", 1) or 1)),
        )

    def get_last_price(self, symbol: str) -> float:
        result = self._request(
            "GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )
        rows = result.get("list") or []
        if not rows:
            raise BybitError(f"no ticker for {symbol}")
        return float(rows[0]["lastPrice"])

    # --- account (signed) ------------------------------------------------------
    def get_wallet_equity_usdt(self) -> float:
        """Unified-account USDT equity. Doubles as a connection/permission test —
        it fails loudly with the exact Bybit message if the keys are wrong."""
        result = self._request(
            "GET", "/v5/account/wallet-balance",
            {"accountType": "UNIFIED", "coin": "USDT"}, signed=True,
        )
        rows = result.get("list") or []
        if not rows:
            return 0.0
        for coin in rows[0].get("coin", []):
            if coin.get("coin") == "USDT":
                return float(coin.get("equity") or coin.get("walletBalance") or 0)
        return float(rows[0].get("totalEquity") or 0)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set both buy & sell leverage for a symbol. Bybit returns retCode 110043
        ('leverage not modified') when it's already at this value — a no-op, not an
        error, so we swallow exactly that code."""
        try:
            self._request(
                "POST", "/v5/position/set-leverage",
                {
                    "category": "linear", "symbol": symbol,
                    "buyLeverage": str(leverage), "sellLeverage": str(leverage),
                },
                signed=True,
            )
        except BybitError as exc:
            if exc.code == 110043:  # leverage already set — fine
                return
            raise

    def place_market_bracket(self, symbol: str, side: str, qty: float,
                             stop_loss: float, take_profit: float | None,
                             order_link_id: str) -> str:
        """Place a market order carrying its stop-loss and (optional) take-profit as
        full-position brackets. Returns Bybit's orderId.

        ``side`` is Bybit's ("Buy" | "Sell"). ``order_link_id`` is our idempotency
        key: Bybit rejects a duplicate with retCode 110072, which the caller treats
        as "already placed" rather than a failure. positionIdx=0 assumes one-way
        mode (the account default); hedge-mode accounts would need 1/2.
        """
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": _fmt(qty),
            "positionIdx": 0,
            "stopLoss": _fmt(stop_loss),
            "tpslMode": "Full",
            "slTriggerBy": "MarkPrice",
            "orderLinkId": order_link_id,
        }
        if take_profit is not None:
            params["takeProfit"] = _fmt(take_profit)
            params["tpTriggerBy"] = "MarkPrice"
        result = self._request("POST", "/v5/order/create", params, signed=True)
        return result.get("orderId", "")

    def place_reduce_limit(self, symbol: str, side: str, qty: float, price: float,
                           order_link_id: str) -> str:
        """Place a reduce-only limit order — one rung of the take-profit ladder. Only
        ever shrinks the position (reduceOnly), so it can never accidentally open or
        flip one. ``side`` is the CLOSING side (Sell for a long, Buy for a short)."""
        result = self._request(
            "POST", "/v5/order/create",
            {
                "category": "linear", "symbol": symbol, "side": side,
                "orderType": "Limit", "qty": _fmt(qty), "price": _fmt(price),
                "reduceOnly": True, "timeInForce": "GTC", "positionIdx": 0,
                "orderLinkId": order_link_id,
            },
            signed=True,
        )
        return result.get("orderId", "")

    def set_stop_loss(self, symbol: str, stop_loss: float) -> None:
        """Move the position's full stop-loss (used to trail to breakeven after the
        first target tags). tpslMode=Full so it covers the whole remaining size."""
        self._request(
            "POST", "/v5/position/trading-stop",
            {
                "category": "linear", "symbol": symbol, "positionIdx": 0,
                "stopLoss": _fmt(stop_loss), "tpslMode": "Full", "slTriggerBy": "MarkPrice",
            },
            signed=True,
        )

    # retCodes that mean "this order is already gone" — a no-op for a cancel, not a
    # failure: 110001 order-not-exists / too-late-to-cancel, 170213 order-not-found.
    _CANCEL_GONE_CODES = {110001, 170213}

    def cancel_order(self, symbol: str, order_link_id: str) -> bool:
        """Cancel one order by its client link id. Returns False (not an error) if the
        order no longer exists — already filled or cancelled."""
        try:
            self._request(
                "POST", "/v5/order/cancel",
                {"category": "linear", "symbol": symbol, "orderLinkId": order_link_id},
                signed=True,
            )
            return True
        except BybitError as exc:
            if exc.code in self._CANCEL_GONE_CODES:
                return False
            raise

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        """Open positions on linear perps. Bybit requires either a symbol or a
        settleCoin; we pass settleCoin=USDT to list them all in one call."""
        params = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = "USDT"
        result = self._request("GET", "/v5/position/list", params, signed=True)
        return result.get("list") or []


def _fmt(x: float) -> str:
    """Bybit wants plain decimal strings, never scientific notation (which
    ``str(1e-05)`` produces and Bybit rejects). Trim trailing zeros for tidiness."""
    s = f"{x:.10f}".rstrip("0").rstrip(".")
    return s or "0"
