"""Hyperliquid candle -> internal normalized shape (Section 6.2).

Normalizing even with a single upstream source keeps the door open to adding
another data source later without reworking the frontend.

  internal shape:
    { "symbol", "time", "open", "high", "low", "close", "volume" }

VERIFIED 2026-06-18 against the live mainnet feed (scripts/verify_hyperliquid.py):
`t` is the OPEN time (aligns exactly to the interval boundary) and `T` is the
CLOSE time (t + interval - 1ms). `t` is used here for `time`, which is the
convention lightweight-charts expects. Numeric fields arrive as strings, hence
the float() coercion. Re-confirm if Hyperliquid changes their WS contract.
"""


def normalize_candle(raw: dict, ticker: str) -> dict:
    """Map a raw Hyperliquid candle dict to the internal shape.

    Raw fields seen across SDKs: t, T, s, i, o, c, h, l, v, n
    (open-time, close-time, symbol, interval, OHLC, volume, trade count).
    Hyperliquid sends numeric values as strings; we coerce to float and
    seconds.
    """
    open_time_ms = int(raw["t"])
    return {
        "symbol": ticker,
        "interval": raw.get("i"),  # candle interval, e.g. "1m"/"5m"/"1h"
        "time": open_time_ms // 1000,  # lightweight-charts wants UNIX seconds
        "open": float(raw["o"]),
        "high": float(raw["h"]),
        "low": float(raw["l"]),
        "close": float(raw["c"]),
        "volume": float(raw["v"]),
    }
