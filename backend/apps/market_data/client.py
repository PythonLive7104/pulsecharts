"""Hyperliquid REST client for historical candle snapshots (Section 9).

Used for the initial chart load (GET /api/symbols/{symbol}/candles/). The live
tail comes over the WS relay instead.

Hyperliquid 'info' endpoint:
    POST https://api.hyperliquid.xyz/info
    {"type": "candleSnapshot",
     "req": {"coin": "BTC", "interval": "1m", "startTime": <ms>, "endTime": <ms>}}

Derived from settings.HYPERLIQUID_WS_URL so testnet/mainnet stay in sync.
"""

import logging
import time
from urllib.parse import urlparse

import requests
from django.conf import settings

from .normalize import normalize_candle

logger = logging.getLogger(__name__)

SUPPORTED_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "8h", "12h", "1d",
}

# Hyperliquid throttles bursts (e.g. a scan firing ~150 calls in seconds), and
# transient 5xx/connection blips happen. Retry those with exponential backoff so
# a throttled request recovers instead of dropping the symbol from the scan.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds: 0.5, 1.0, 2.0
_BACKOFF_CAP = 5.0


def _info_url() -> str:
    # wss://api.hyperliquid.xyz/ws -> https://api.hyperliquid.xyz/info
    host = urlparse(settings.HYPERLIQUID_WS_URL).hostname or "api.hyperliquid.xyz"
    return f"https://{host}/info"


def _post_info(payload: dict, *, timeout: float = 10.0) -> requests.Response:
    """POST to the Hyperliquid info endpoint with retry/backoff on throttling and
    transient errors. Raises the last error if all attempts fail."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.post(_info_url(), json=payload, timeout=timeout)
            if resp.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES:
                # Honor Retry-After when present, else exponential backoff.
                try:
                    wait = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    wait = _BACKOFF_BASE * (2 ** attempt)
                wait = min(wait, _BACKOFF_CAP)
                logger.warning(
                    "Hyperliquid %s — retry %d/%d in %.1fs (%s)",
                    resp.status_code, attempt + 1, _MAX_RETRIES, wait,
                    payload.get("req", {}).get("coin", payload.get("type")),
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP))
                continue
            raise
    # Exhausted retries on a retryable status code.
    if last_exc:
        raise last_exc
    raise requests.RequestException("Hyperliquid request failed after retries")


def fetch_perp_universe(*, timeout: float = 10.0) -> list[dict]:
    """Return Hyperliquid's perpetual universe (Section 6, 16).

    POST /info {"type": "meta"} -> {"universe": [{"name": "BTC", "isDelisted": ...}, ...]}.
    Each entry is a perp coin; `name` is the WS subscription `coin` code. Used by
    the sync_symbols command to populate the Symbol table directly from the
    source of truth, so coverage stays in step with what's actually listed.
    """
    resp = _post_info({"type": "meta"}, timeout=timeout)
    return (resp.json() or {}).get("universe", [])


def fetch_candles(
    coin: str,
    ticker: str,
    interval: str = "1m",
    limit: int = 500,
    *,
    timeout: float = 10.0,
) -> list[dict]:
    """Return up to `limit` normalized candles, oldest first."""
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")

    end_ms = int(time.time() * 1000)
    # Rough window; Hyperliquid caps the response server-side regardless.
    start_ms = end_ms - limit * _interval_ms(interval)

    resp = _post_info(
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=timeout,
    )
    raw_candles = resp.json() or []
    return [normalize_candle(c, ticker) for c in raw_candles][-limit:]


def _interval_ms(interval: str) -> int:
    unit = interval[-1]
    qty = int(interval[:-1])
    factor = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]
    return qty * factor


def fetch_all_mids(*, timeout: float = 10.0) -> dict[str, float]:
    """All current mid prices in one call: {coin: price} (Section 6.1 allMids).

    Used by the price-alert checker — one request covers every symbol.
    """
    resp = _post_info({"type": "allMids"}, timeout=timeout)
    out = {}
    for coin, price in (resp.json() or {}).items():
        try:
            out[coin] = float(price)
        except (TypeError, ValueError):
            continue
    return out


def fetch_candles_since(coin: str, ticker: str, interval: str, start_ms: int, *, timeout: float = 10.0) -> list[dict]:
    """Normalized candles from `start_ms` to now (oldest first).

    Used by the signal-outcome evaluator to see what price did after a signal
    was generated.
    """
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")
    end_ms = int(time.time() * 1000)
    resp = _post_info(
        {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        },
        timeout=timeout,
    )
    return [normalize_candle(c, ticker) for c in (resp.json() or [])]
