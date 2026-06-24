"""Source-agnostic candle dispatch.

One entry point so callers (candles endpoint, signal engine, evaluator) don't
branch on market type themselves: given a Symbol, route to Hyperliquid (crypto)
or Twelve Data (forex) and return candles in the same internal shape either way.
"""

from . import client, forex
from .models import Symbol


def supported_intervals(symbol: Symbol) -> set[str]:
    return forex.SUPPORTED_INTERVALS if symbol.is_forex else client.SUPPORTED_INTERVALS


def get_candles(symbol: Symbol, interval: str = "1h", limit: int = 300) -> list[dict]:
    """Normalized candles (oldest first) for any symbol."""
    if symbol.is_forex:
        return forex.fetch_forex_candles(symbol.feed_symbol, symbol.ticker, interval, limit)
    return client.fetch_candles(symbol.hl_coin, symbol.ticker, interval, limit)


def get_candles_since(symbol: Symbol, interval: str, start_ms: int) -> list[dict]:
    """Normalized candles from `start_ms` to now (oldest first) for any symbol."""
    if symbol.is_forex:
        return forex.fetch_forex_candles_since(symbol.feed_symbol, symbol.ticker, interval, start_ms)
    return client.fetch_candles_since(symbol.hl_coin, symbol.ticker, interval, start_ms)
