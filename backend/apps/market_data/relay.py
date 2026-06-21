"""Hyperliquid upstream relay (Section 6, 7, 16).

Maintains a single upstream WS connection to Hyperliquid and relays candles into
the Redis-backed Channels group for each (symbol, interval). Browser clients
never talk to Hyperliquid directly — they connect to /ws/market/ and receive
these broadcasts.

Demand-driven (Section 7): the relay only subscribes upstream to the
(symbol, interval) topics that have at least one active browser client, tracked
via the Redis demand registry (demand.py). A short reconcile loop diffs "what
clients want" against "what we're subscribed to" and sends the upstream
subscribe/unsubscribe deltas — so each chart gets live candles at the timeframe
it's actually displaying, and we only stream what's on screen.

Reconnect/backoff is built in from the start (Section 16).

Run it with:  python manage.py run_relay
"""

import asyncio
import json
import logging

import websockets
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from . import demand
from .consumers import group_name
from .models import Symbol
from .normalize import normalize_candle

logger = logging.getLogger("market_data.relay")

# How often to reconcile upstream subscriptions against client demand.
RECONCILE_INTERVAL = 2.0

# Reconnect backoff bounds (seconds).
_BACKOFF_START = 1.0
_BACKOFF_MAX = 30.0


@sync_to_async
def _active_symbol_map() -> dict[str, str]:
    """ticker -> hl_coin for active symbols (refreshed each reconcile so newly
    synced coins are picked up without a relay restart)."""
    return {
        ticker: coin
        for ticker, coin in Symbol.objects.filter(is_active=True).values_list(
            "ticker", "hl_coin"
        )
    }


def _sub_msg(coin: str, interval: str, method: str) -> str:
    return json.dumps(
        {
            "method": method,  # "subscribe" | "unsubscribe"
            "subscription": {"type": "candle", "coin": coin, "interval": interval},
        }
    )


async def _broadcast(channel_layer, ticker: str, interval: str, candle: dict) -> None:
    await channel_layer.group_send(
        group_name(ticker, interval),
        {"type": "candle.message", "data": candle},
    )


async def _reconcile_loop(ws, current: dict) -> None:
    """Keep upstream subscriptions in sync with client demand.

    `current` maps topic "ticker|interval" -> (coin, interval), mutated in place
    so the candle reader can resolve the ticker for an incoming candle.
    """
    while True:
        symbol_map = await _active_symbol_map()
        wanted = {}
        for topic in await demand.demanded():
            ticker, _, interval = topic.partition("|")
            coin = symbol_map.get(ticker)
            if coin and interval:
                wanted[topic] = (coin, interval)

        to_add = wanted.keys() - current.keys()
        to_remove = current.keys() - wanted.keys()

        for topic in to_add:
            coin, interval = wanted[topic]
            await ws.send(_sub_msg(coin, interval, "subscribe"))
            current[topic] = (coin, interval)
        for topic in list(to_remove):
            coin, interval = current.pop(topic)
            await ws.send(_sub_msg(coin, interval, "unsubscribe"))

        if to_add or to_remove:
            logger.info(
                "Upstream subs: +%d -%d (now watching %d topics)",
                len(to_add), len(to_remove), len(current),
            )
        await asyncio.sleep(RECONCILE_INTERVAL)


async def _read_candles(ws, channel_layer, current: dict) -> None:
    """Relay incoming candles to the right (symbol, interval) Channels group."""
    def ticker_for(coin, interval):
        for topic, (c, i) in current.items():
            if c == coin and i == interval:
                return topic.partition("|")[0]
        return None

    async for raw in ws:
        msg = json.loads(raw)
        if msg.get("channel") != "candle":
            continue  # ignore acks / other channels
        data = msg.get("data") or {}
        ticker = ticker_for(data.get("s"), data.get("i"))
        if not ticker:
            continue  # candle for a topic we just unsubscribed from
        try:
            candle = normalize_candle(data, ticker)
        except (KeyError, ValueError, TypeError):
            logger.exception("Failed to normalize candle: %s", data)
            continue
        await _broadcast(channel_layer, ticker, data["i"], candle)


async def _run_once() -> None:
    """One full connection lifecycle. Returns/raises on disconnect for backoff."""
    channel_layer = get_channel_layer()
    current: dict = {}  # topic "ticker|interval" -> (coin, interval)

    async with websockets.connect(settings.HYPERLIQUID_WS_URL) as ws:
        logger.info("Connected upstream; reconciling against client demand.")
        reader = asyncio.create_task(_read_candles(ws, channel_layer, current))
        reconciler = asyncio.create_task(_reconcile_loop(ws, current))
        try:
            done, pending = await asyncio.wait(
                {reader, reconciler}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()  # surface any exception
        finally:
            for task in (reader, reconciler):
                task.cancel()


async def run_relay_forever() -> None:
    """Reconnect loop with exponential backoff (Section 16)."""
    backoff = _BACKOFF_START
    while True:
        try:
            await _run_once()
            backoff = _BACKOFF_START
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Relay connection dropped; reconnecting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
