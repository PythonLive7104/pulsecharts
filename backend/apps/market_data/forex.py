"""Yahoo Finance forex client — historical + recent candle snapshots.

Forex counterpart to client.py (Hyperliquid). Same job: return candles in the
internal normalized shape (Section 6.2) so the chart, relay, and signal engine
don't care which market a symbol comes from.

Why Yahoo: it's a data source, not a broker, so there's no account/KYC and no
regional restriction (broker APIs like OANDA aren't available in every country).
It needs no API key. Trade-off: it's an unofficial/public endpoint — fine for
low volume, but it can rate-limit under load and isn't contractually guaranteed.
Disable via FOREX_ENABLED if it becomes unreliable.

Yahoo chart endpoint:
    GET https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X
        ?interval=1h&range=3mo            (or &period1=<unix>&period2=<unix>)
    -> {"chart": {"result": [{"timestamp": [...],
                              "indicators": {"quote": [{"open","high","low",
                                                        "close","volume"}]}}]}}
    Timestamps are UNIX seconds (UTC), oldest first; some rows can be null (gaps)
    and are skipped. Yahoo has no 4h granularity, so 4h is aggregated from 1h.

Instruments use Yahoo's FX ticker form ("EURUSD=X"), stored in Symbol.feed_symbol.
"""

import logging
import time
from datetime import datetime, timezone

import requests
from django.conf import settings

logger = logging.getLogger("market_data.forex")

_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Yahoo rejects requests without a browser-like User-Agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PulseCharts/1.0)"}

# Internal interval -> Yahoo interval. 4h has no Yahoo equivalent and is built by
# aggregating 1h candles (see _aggregate_4h).
_YF_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "1d": "1d",
}
SUPPORTED_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}

# Lookback window per interval for the default (count-based) fetch. Bounded by
# Yahoo's intraday history limits (1m<=7d, 5–30m<=60d, 1h<=730d).
_RANGE = {
    "1m": "5d", "5m": "1mo", "15m": "1mo", "30m": "1mo",
    "1h": "3mo", "4h": "6mo", "1d": "1y",
}

_INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}
_4H_SECONDS = 4 * 3600

FOREX_ENABLED = settings.FOREX_ENABLED


def _parse_result(result: dict, ticker: str, interval: str) -> list[dict]:
    """Yahoo chart `result` block -> normalized candles (oldest first)."""
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []

    out = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            break
        if None in (o, h, l, c):
            continue  # Yahoo leaves gaps as null
        out.append({
            "symbol": ticker,
            "interval": interval,
            "time": int(t),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0,
        })
    return out


def _download(yahoo_symbol: str, yf_interval: str, ticker: str, interval: str, *,
              range_: str | None = None, period1: int | None = None,
              period2: int | None = None, timeout: float) -> list[dict]:
    params = {"interval": yf_interval}
    if period1 is not None:
        params["period1"] = int(period1)
        params["period2"] = int(period2 if period2 is not None else time.time())
    else:
        params["range"] = range_
    resp = requests.get(
        _BASE_URL + yahoo_symbol, params=params, headers=_HEADERS, timeout=timeout
    )
    resp.raise_for_status()
    chart = (resp.json() or {}).get("chart") or {}
    if chart.get("error"):
        raise requests.RequestException(f"Yahoo error for {yahoo_symbol}: {chart['error']}")
    results = chart.get("result") or []
    return _parse_result(results[0], ticker, interval) if results else []


def _aggregate_4h(candles_1h: list[dict]) -> list[dict]:
    """Roll oldest-first 1h candles into 4h buckets aligned to 00/04/08/12/16/20
    UTC (epoch divides evenly into 4h, so time - time%14400 lands on a boundary)."""
    buckets: dict[int, dict] = {}
    for c in candles_1h:
        start = c["time"] - (c["time"] % _4H_SECONDS)
        agg = buckets.get(start)
        if agg is None:
            buckets[start] = {
                "symbol": c["symbol"], "interval": "4h", "time": start,
                "open": c["open"], "high": c["high"], "low": c["low"],
                "close": c["close"], "volume": c["volume"],
            }
        else:
            agg["high"] = max(agg["high"], c["high"])
            agg["low"] = min(agg["low"], c["low"])
            agg["close"] = c["close"]
            agg["volume"] += c["volume"]
    return [buckets[k] for k in sorted(buckets)]


def fetch_forex_candles(yahoo_symbol: str, ticker: str, interval: str = "1h",
                        limit: int = 300, *, timeout: float = 10.0) -> list[dict]:
    """Up to `limit` normalized forex candles, oldest first."""
    if not FOREX_ENABLED:
        logger.info("Forex disabled (FOREX_ENABLED=False); %s skipped.", yahoo_symbol)
        return []
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported forex interval: {interval}")
    if interval == "4h":
        base = _download(yahoo_symbol, "1h", ticker, "1h", range_=_RANGE["4h"], timeout=timeout)
        candles = _aggregate_4h(base)
    else:
        candles = _download(
            yahoo_symbol, _YF_INTERVAL[interval], ticker, interval,
            range_=_RANGE[interval], timeout=timeout,
        )
    return candles[-limit:]


def fetch_forex_candles_since(yahoo_symbol: str, ticker: str, interval: str,
                              start_ms: int, *, timeout: float = 10.0) -> list[dict]:
    """Normalized forex candles from `start_ms` to now (oldest first).

    Mirrors client.fetch_candles_since for the signal-outcome evaluator."""
    if not FOREX_ENABLED:
        return []
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported forex interval: {interval}")
    p1 = start_ms // 1000
    if interval == "4h":
        base = _download(yahoo_symbol, "1h", ticker, "1h", period1=p1, timeout=timeout)
        return _aggregate_4h(base)
    return _download(
        yahoo_symbol, _YF_INTERVAL[interval], ticker, interval, period1=p1, timeout=timeout
    )


def fetch_forex_latest(yahoo_symbol: str, ticker: str, interval: str, *,
                       timeout: float = 10.0) -> list[dict]:
    """Just the last few candles for the live relay tail — a minimal time-windowed
    fetch so the 15s poll doesn't re-download a wide history each cycle."""
    if not FOREX_ENABLED:
        return []
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported forex interval: {interval}")
    now = int(time.time())
    if interval == "4h":
        base = _download(yahoo_symbol, "1h", ticker, "1h",
                         period1=now - 3 * _4H_SECONDS, period2=now, timeout=timeout)
        return _aggregate_4h(base)
    p1 = now - 4 * _INTERVAL_SECONDS[interval]
    return _download(yahoo_symbol, _YF_INTERVAL[interval], ticker, interval,
                     period1=p1, period2=now, timeout=timeout)


def fetch_forex_price(yahoo_symbol: str, *, timeout: float = 10.0) -> float | None:
    """Latest price for one forex pair (for the price-alert checker). Reads
    `meta.regularMarketPrice` off the chart endpoint — same no-auth endpoint as
    the candle fetches, one request per pair. Returns None if unavailable."""
    if not FOREX_ENABLED:
        return None
    resp = requests.get(
        _BASE_URL + yahoo_symbol,
        params={"interval": "1h", "range": "1d"},
        headers=_HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    chart = (resp.json() or {}).get("chart") or {}
    if chart.get("error"):
        raise requests.RequestException(f"Yahoo error for {yahoo_symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        return None
    price = (results[0].get("meta") or {}).get("regularMarketPrice")
    return float(price) if price is not None else None


def market_open(now: datetime | None = None) -> bool:
    """True if the forex market is open (it trades ~24/5, closed weekends).

    Approximation in UTC: open from Sunday 21:00 to Friday 21:00. Good enough to
    stop the signal engine generating setups on stale weekend candles."""
    now = now or datetime.now(timezone.utc)
    wd = now.weekday()  # Mon=0 .. Sun=6
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday — opens 21:00 UTC
        return now.hour >= 21
    if wd == 4:  # Friday — closes 21:00 UTC
        return now.hour < 21
    return True
