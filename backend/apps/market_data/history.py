"""Long-history candles for research — Binance's public kline archive (newPRD §11, §12).

Hyperliquid's candle API only reaches back a few thousand bars, so a backtest on it
sees weeks to months: one regime, not "multiple market cycles". Binance publishes
every spot kline since 2017 as monthly zip files at data.binance.vision — static,
free, no account or key, each with a published SHA-256.

Research only. Live charts, signals and evaluation still run on the live feeds
(apps.market_data.feeds); nothing here is on a request path. BTCUSDT on Binance and
BTC on Hyperliquid track each other closely but are different venues, so a backtest
on this data measures the strategy, not Hyperliquid's exact prints.

    files:  <HISTORY_DIR>/<PAIR>/<interval>/<PAIR>-<interval>-<YYYY-MM>.zip
"""

from __future__ import annotations

import csv
import hashlib
import io
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from django.conf import settings

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

# Intervals Binance archives that the product also uses.
INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d")


class HistoryError(Exception):
    pass


def history_dir(override: str | None = None) -> Path:
    return Path(override or settings.HISTORY_DIR)


def pair_for(symbol) -> str | None:
    """Binance spot pair for a Symbol, or None when there's no archive equivalent.

    Crypto only: Hyperliquid coin "BTC" -> "BTCUSDT". Forex has no Binance history.
    """
    if getattr(symbol, "is_forex", False):
        return None
    coin = (getattr(symbol, "hl_coin", "") or "").upper()
    return f"{coin}USDT" if coin.isalnum() else None


def months(start: date, end: date):
    """Every (year, month) from start's month through end's month, inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _month_path(root: Path, pair: str, interval: str, y: int, m: int) -> Path:
    return root / pair / interval / f"{pair}-{interval}-{y:04d}-{m:02d}.zip"


RETRY_DELAYS = (2, 5, 15)  # seconds between attempts; len + 1 attempts in total


def _get_with_retry(http, url: str, timeout: int, sleep=time.sleep):
    """GET that rides out the transient failures a bulk download WILL hit.

    The archive's CDN resets connections and rate-limits (429) under sustained
    load; one dropped connection used to abort a whole multi-pair fetch. Connection
    errors, timeouts, 429 and 5xx are retried with backoff. A 404 (month not
    archived) or other 4xx is an answer, not a failure, and returns immediately.
    """
    for delay in (*RETRY_DELAYS, None):
        try:
            resp = http.get(url, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if delay is None:
                raise
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            if delay is None:
                return resp  # caller's raise_for_status reports it
        sleep(delay)


def download_month(pair: str, interval: str, y: int, m: int, root: Path,
                   session: requests.Session | None = None) -> str:
    """Fetch one monthly archive. Returns 'cached' | 'downloaded' | 'missing'.

    'missing' (HTTP 404) is normal: months before a pair listed, or the current
    month, which Binance only archives once it has ended. The SHA-256 is checked
    against Binance's .CHECKSUM before the file is kept, so a truncated download can
    never sit in the archive looking like real data.
    """
    path = _month_path(root, pair, interval, y, m)
    if path.exists():
        return "cached"
    http = session or requests
    url = f"{BASE_URL}/{pair}/{interval}/{path.name}"
    resp = _get_with_retry(http, url, timeout=60)
    if resp.status_code == 404:
        return "missing"
    resp.raise_for_status()

    check = _get_with_retry(http, f"{url}.CHECKSUM", timeout=30)
    check.raise_for_status()
    expected = check.text.split()[0].strip().lower()
    actual = hashlib.sha256(resp.content).hexdigest()
    if actual != expected:
        raise HistoryError(f"checksum mismatch for {path.name}: {actual} != {expected}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".zip.tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(path)  # atomic: a killed download can't leave a half-written zip
    return "downloaded"


def _open_seconds(raw: str) -> int:
    """Kline open time in UNIX seconds.

    Binance switched its spot archive from milliseconds to MICROseconds in 2025;
    reading one as the other dates a bar ~50,000 years out, so both are normalised.
    """
    t = int(raw)
    if t >= 10**14:      # microseconds
        return t // 1_000_000
    return t // 1_000    # milliseconds


def _parse_zip(path: Path, pair: str, interval: str) -> list[dict]:
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        text = zf.read(name).decode()
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue  # some months ship a header row
        out.append({
            # Same shape as normalize.normalize_candle, so the engine can't tell
            # archive candles from live ones.
            "symbol": pair,
            "interval": interval,
            "time": _open_seconds(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return out


def load(pair: str, interval: str, start_ts: int | None = None, end_ts: int | None = None,
         root: Path | None = None) -> list[dict]:
    """All archived candles for (pair, interval) with start_ts <= time < end_ts,
    oldest first, de-duplicated on open time."""
    folder = (root or history_dir()) / pair / interval
    if not folder.is_dir():
        return []
    by_time: dict[int, dict] = {}
    for path in sorted(folder.glob(f"{pair}-{interval}-*.zip")):
        for c in _parse_zip(path, pair, interval):
            by_time[c["time"]] = c
    return [
        by_time[t] for t in sorted(by_time)
        if (start_ts is None or t >= start_ts) and (end_ts is None or t < end_ts)
    ]


def gaps(candles: list[dict], interval_seconds: int) -> list[tuple[int, int]]:
    """(from_time, missing_bars) wherever consecutive candles skip bars.

    Binance has real outages in its history (exchange maintenance). Reported, never
    filled: inventing bars would be inventing prices.
    """
    out = []
    for a, b in zip(candles, candles[1:]):
        step = b["time"] - a["time"]
        if step > interval_seconds:
            out.append((a["time"], step // interval_seconds - 1))
    return out


def parse_day(value: str) -> int:
    """'YYYY-MM-DD' -> UNIX seconds at 00:00 UTC."""
    try:
        d = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HistoryError(f"expected a date as YYYY-MM-DD, got {value!r}") from None
    return int(d.timestamp())
