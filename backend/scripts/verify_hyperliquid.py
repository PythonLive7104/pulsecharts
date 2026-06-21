"""Verify the live Hyperliquid candle feed and our parser (Section 6.1).

Standalone — does NOT need Django. Connects to the live WS, subscribes to a
candle stream, captures a few messages, and prints:

  1. The raw candle payload (so we can see actual field names/values).
  2. Our normalized output (apps/market_data/normalize.py logic, inlined).
  3. A t-vs-T analysis: which timestamp is open-time vs close-time, by checking
     which one aligns to the interval boundary and the gap between them.

Run:
    ../.venv/bin/python scripts/verify_hyperliquid.py --coin BTC --interval 1m --count 3
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone

import websockets

WS_URL = "wss://api.hyperliquid.xyz/ws"

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def analyze_timestamps(raw: dict, interval: str) -> None:
    t, T = int(raw["t"]), int(raw["T"])
    step = INTERVAL_MS.get(interval)
    print("  t =", t, _iso(t), "| aligned to interval:", t % step == 0 if step else "?")
    print("  T =", T, _iso(T), "| aligned to interval:", T % step == 0 if step else "?")
    print(f"  T - t = {(T - t) / 1000:.0f}s (interval is {step // 1000 if step else '?'}s)")
    # The open-time aligns to the bucket boundary; close-time = open + interval (-1ms).
    if step:
        if t % step == 0:
            print("  => 't' is OPEN time (aligns to bucket). normalize.py is CORRECT.")
        elif T % step == 0:
            print("  => 'T' is the aligned one — normalize.py would be WRONG; swap to T.")
        else:
            print("  => neither aligns cleanly; inspect manually.")


def normalize(raw: dict, ticker: str) -> dict:
    """Mirror of apps/market_data/normalize.py (kept inline to avoid Django)."""
    return {
        "symbol": ticker,
        "time": int(raw["t"]) // 1000,
        "open": float(raw["o"]),
        "high": float(raw["h"]),
        "low": float(raw["l"]),
        "close": float(raw["c"]),
        "volume": float(raw["v"]),
    }


async def main(coin: str, interval: str, count: int) -> None:
    sub = {
        "method": "subscribe",
        "subscription": {"type": "candle", "coin": coin, "interval": interval},
    }
    print(f"Connecting to {WS_URL} …")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps(sub))
        print(f"Subscribed: {coin} {interval}\n")
        seen = 0
        async for raw in ws:
            msg = json.loads(raw)
            ch = msg.get("channel")
            if ch == "subscriptionResponse":
                print("ACK:", json.dumps(msg["data"]), "\n")
                continue
            if ch != "candle":
                continue
            data = msg["data"]
            seen += 1
            print(f"--- candle #{seen} ---")
            print("RAW   :", json.dumps(data))
            print("NORM  :", json.dumps(normalize(data, f"{coin}-USD")))
            analyze_timestamps(data, interval)
            print()
            if seen >= count:
                break
    print("Done.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--coin", default="BTC")
    p.add_argument("--interval", default="1m")
    p.add_argument("--count", type=int, default=3)
    args = p.parse_args()
    asyncio.run(main(args.coin, args.interval, args.count))
