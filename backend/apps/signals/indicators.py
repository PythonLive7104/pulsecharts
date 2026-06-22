"""Server-side indicator computation for the signal engine (Section 20.1).

Unlike chart indicators (computed client-side, Section 10), the signal engine
runs on a schedule with no browser, so it needs the indicator values in Python.

Implemented as dependency-free pure Python (ports of the verified frontend
formulas in frontend/src/lib/indicators.js) rather than pandas_ta — pandas_ta has
known breakage on numpy >= 2.0 (e.g. removed np.NaN), so a small self-contained
module is more robust for a solo deploy.

`compute_indicators(candles)` takes normalized candles (oldest first):
    { time, open, high, low, close, volume }
and returns the single most-recent snapshot dict the Claude prompt expects
(Section 20.3 build_signal_prompt).
"""

from __future__ import annotations


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    gain /= period
    loss /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def _macd(closes: list[float], fast=12, slow=26, signal=9):
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    macd_line = [
        (ef[i] - es[i]) if ef[i] is not None and es[i] is not None else None
        for i in range(len(closes))
    ]
    vals = [v for v in macd_line if v is not None]
    sig = _ema(vals, signal)
    macd_now = macd_line[-1]
    signal_now = sig[-1] if sig else None
    hist = (macd_now - signal_now) if macd_now is not None and signal_now is not None else None
    return macd_now, signal_now, hist


def _bollinger(closes: list[float], period=20, mult=2):
    mid = _sma(closes, period)
    if mid is None:
        return None, None, None
    window = closes[-period:]
    var = sum((v - mid) ** 2 for v in window) / period
    sd = var ** 0.5
    return mid + mult * sd, mid, mid - mult * sd


def _atr(candles: list[dict], period=14) -> float | None:
    if len(candles) <= period:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def _stochastic(candles: list[dict], k_period=14, d_period=3):
    if len(candles) < k_period:
        return None, None
    ks = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1 : i + 1]
        hh = max(c["high"] for c in window)
        ll = min(c["low"] for c in window)
        ks.append(100.0 if hh == ll else 100 * (candles[i]["close"] - ll) / (hh - ll))
    k_now = ks[-1]
    d_now = sum(ks[-d_period:]) / d_period if len(ks) >= d_period else None
    return k_now, d_now


def _vwap_session(candles: list[dict]) -> float | None:
    """VWAP over the latest UTC day present in the buffer."""
    if not candles:
        return None
    last_day = candles[-1]["time"] // 86400
    cum_pv = cum_v = 0.0
    for c in candles:
        if c["time"] // 86400 != last_day:
            continue
        typical = (c["high"] + c["low"] + c["close"]) / 3
        cum_pv += typical * c["volume"]
        cum_v += c["volume"]
    return cum_pv / cum_v if cum_v else None


def _swings(candles: list[dict], lookback=50):
    window = candles[-lookback:]
    return max(c["high"] for c in window), min(c["low"] for c in window)


def compute_indicators(candles: list[dict]) -> dict:
    """Snapshot of indicator values for the most recent completed candle.

    The feed's latest bucket is the still-forming candle, whose OHLCV jitters
    intrabar. Drop it and compute on the last CLOSED candle so signals don't
    flip-flop between scans — and so generation matches outcome evaluation,
    which also only acts once a candle has closed.
    """
    if len(candles) >= 2:
        candles = candles[:-1]
    closes = [c["close"] for c in candles]
    last = candles[-1]
    macd_line, macd_signal, macd_hist = _macd(closes)
    bb_u, bb_m, bb_l = _bollinger(closes)
    stoch_k, stoch_d = _stochastic(candles)
    swing_high, swing_low = _swings(candles)
    day = last["time"] // 86400
    day_candles = [c for c in candles if c["time"] // 86400 == day]

    return {
        "close": last["close"],
        "high_24h": max((c["high"] for c in day_candles), default=last["high"]),
        "low_24h": min((c["low"] for c in day_candles), default=last["low"]),
        "volume": last["volume"],
        "volume_ma20": _sma([c["volume"] for c in candles], 20),
        "ema9": _ema(closes, 9)[-1],
        "ema21": _ema(closes, 21)[-1],
        "ema50": _ema(closes, 50)[-1],
        "rsi": _rsi(closes),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "bb_upper": bb_u,
        "bb_mid": bb_m,
        "bb_lower": bb_l,
        "atr": _atr(candles),
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "vwap": _vwap_session(candles),
        "swing_high": swing_high,
        "swing_low": swing_low,
    }
