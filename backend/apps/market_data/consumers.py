"""Browser-facing WebSocket consumer (Section 7, 9).

Each browser connects once to /ws/market/ and subscribes/unsubscribes to
(symbol, interval) topics as the user switches charts/timeframes. The upstream
relay (see relay.py) pushes normalized candles into Redis-backed Channels groups
keyed by symbol+interval; this consumer joins/leaves those groups and forwards
messages to the socket.

Client -> server messages:
    {"action": "subscribe",   "symbol": "BTC-USD", "interval": "5m"}
    {"action": "unsubscribe", "symbol": "BTC-USD", "interval": "5m"}
(interval defaults to "1m" if omitted.)

Server -> client messages:
    {"type": "candle", "data": { ...normalized candle incl. interval... }}
"""

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from . import demand

DEFAULT_INTERVAL = "1m"


def group_name(symbol: str, interval: str) -> str:
    # Channels group names allow alphanumerics, -, _, . — all safe here.
    return f"market.{symbol}.{interval}"


def topic(symbol: str, interval: str) -> str:
    """Demand-registry key for a (symbol, interval) subscription."""
    return f"{symbol}|{interval}"


class MarketConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.subscribed: set[tuple[str, str]] = set()
        await self.accept()

    async def disconnect(self, code):
        for symbol, interval in list(self.subscribed):
            await self.channel_layer.group_discard(
                group_name(symbol, interval), self.channel_name
            )
            await demand.decrement(topic(symbol, interval))
        self.subscribed.clear()

    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        symbol = content.get("symbol")
        interval = content.get("interval") or DEFAULT_INTERVAL
        if not symbol or action not in {"subscribe", "unsubscribe"}:
            await self.send_json({"type": "error", "detail": "bad request"})
            return

        key = (symbol, interval)
        if action == "subscribe":
            if key in self.subscribed:
                return  # idempotent — don't double-count demand
            await self.channel_layer.group_add(
                group_name(symbol, interval), self.channel_name
            )
            self.subscribed.add(key)
            await demand.increment(topic(symbol, interval))
            await self.send_json({"type": "subscribed", "symbol": symbol, "interval": interval})
        else:
            if key not in self.subscribed:
                return
            await self.channel_layer.group_discard(
                group_name(symbol, interval), self.channel_name
            )
            self.subscribed.discard(key)
            await demand.decrement(topic(symbol, interval))
            await self.send_json({"type": "unsubscribed", "symbol": symbol, "interval": interval})

    # --- group event handlers (called via group_send type="candle.message") ---

    async def candle_message(self, event):
        await self.send_json({"type": "candle", "data": event["data"]})
