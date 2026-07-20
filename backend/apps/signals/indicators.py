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


def _adx(candles: list[dict], period=14) -> float | None:
    """Wilder's Average Directional Index — trend *strength* (direction-agnostic).

    Rule of thumb: ADX < ~20 = ranging/choppy, > ~25 = trending. Used by the
    regime filter to keep trend strategies out of sideways markets.
    """
    if len(candles) < period * 2 + 1:
        return None
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    def _wilder(vals):
        # Wilder running sum (RMA-style), one value per input from index period-1.
        s = sum(vals[:period])
        out = [s]
        for v in vals[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    atr_s, pdm_s, mdm_s = _wilder(trs), _wilder(plus_dm), _wilder(minus_dm)
    dxs = []
    for atr_v, pdm_v, mdm_v in zip(atr_s, pdm_s, mdm_s):
        if atr_v == 0:
            dxs.append(0.0)
            continue
        pdi = 100 * pdm_v / atr_v
        mdi = 100 * mdm_v / atr_v
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom else 0.0)

    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return round(adx, 2)


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


def _swings(candles: list[dict], lookback=60, span=2):
    """Most RECENT local swing high/low (fractal pivots), not the 50-bar absolute
    extreme. A pivot high is a bar whose high is >= the `span` bars on each side;
    pivot low is the mirror. Scanning back from the latest confirmed pivot finds
    the nearest structure — e.g. for a trend-continuation SELL, the last pullback
    high just above price — so the stop hugs real structure instead of anchoring to
    a far, already-broken extreme (which produced absurd 8%+ stops). Falls back to
    the absolute extreme if no pivot forms in the window."""
    window = candles[-lookback:]
    n = len(window)
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    swing_high = swing_low = None
    # Walk newest-to-oldest over bars that have `span` neighbours on each side.
    for i in range(n - 1 - span, span - 1, -1):
        if swing_high is None and all(
            highs[i] >= highs[j] for j in range(i - span, i + span + 1) if j != i
        ):
            swing_high = highs[i]
        if swing_low is None and all(
            lows[i] <= lows[j] for j in range(i - span, i + span + 1) if j != i
        ):
            swing_low = lows[i]
        if swing_high is not None and swing_low is not None:
            break
    if swing_high is None:
        swing_high = max(highs)
    if swing_low is None:
        swing_low = min(lows)
    return swing_high, swing_low


def _swing_leg(candles: list[dict], lookback=60, span=2):
    """Most recent completed impulse leg, for Fibonacci-retracement gating.

    Finds the nearest fractal pivot high and nearest fractal pivot low in the
    window (same span logic as `_swings`), keeping their positions. Whichever pivot
    is MORE RECENT is the end of the current leg:
      - pivot high newer than pivot low  → the last move was UP (low→high); price is
        now retracing DOWN from the high  → an uptrend pullback (favours BUY).
      - pivot low newer than pivot high  → the last move was DOWN (high→low); price
        is retracing UP → a downtrend pullback (favours SELL).

    Returns (leg_high, leg_low, newer_is_high) or None when a clean pivot pair
    isn't present or the leg is degenerate (high <= low).
    """
    window = candles[-lookback:]
    n = len(window)
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    ph_idx = pl_idx = None
    # Walk newest-to-oldest over bars that have `span` neighbours on each side.
    for i in range(n - 1 - span, span - 1, -1):
        if ph_idx is None and all(
            highs[i] >= highs[j] for j in range(i - span, i + span + 1) if j != i
        ):
            ph_idx = i
        if pl_idx is None and all(
            lows[i] <= lows[j] for j in range(i - span, i + span + 1) if j != i
        ):
            pl_idx = i
        if ph_idx is not None and pl_idx is not None:
            break
    if ph_idx is None or pl_idx is None:
        return None
    leg_high, leg_low = highs[ph_idx], lows[pl_idx]
    if leg_high <= leg_low:
        return None
    return leg_high, leg_low, ph_idx > pl_idx


def _pivots(candles: list[dict], lookback=120, span=2):
    """All fractal pivot highs/lows in the window, oldest→newest, as (index, price).

    Same fractal test as `_swings` (a bar whose high/low is the extreme of the
    `span` bars on each side), but collects the FULL sequence rather than just the
    nearest one — market-structure classification needs consecutive pivots to
    compare, not a single extreme.
    """
    window = candles[-lookback:]
    n = len(window)
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    pivot_highs, pivot_lows = [], []
    for i in range(span, n - span):
        if all(highs[i] >= highs[j] for j in range(i - span, i + span + 1) if j != i):
            pivot_highs.append((i, highs[i]))
        if all(lows[i] <= lows[j] for j in range(i - span, i + span + 1) if j != i):
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def _market_structure(candles: list[dict], lookback=120, span=2):
    """Swing-structure trend read from the last two confirmed pivots on each side.

    Classic price-structure definition of trend, independent of any moving average:
      - "up"   : Higher High AND Higher Low  (last pivot high > prior, last pivot low > prior)
      - "down" : Lower High  AND Lower Low
      - None   : mixed (HH+LL expansion / LH+HL contraction) or too few pivots — an
                 ambiguous/ranging structure, deliberately no trend call.

    Returns (structure, last_high, prev_high, last_low, prev_low). The pivot prices
    are handy for the card's reasoning/invalidation text. Needs >= 2 pivots of each
    type; returns (None, ...) otherwise. Note the newest fractal only confirms `span`
    bars after it forms, so structure lags slightly — acceptable on 1h/4h/1d.
    """
    pivot_highs, pivot_lows = _pivots(candles, lookback, span)
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return None, None, None, None, None
    last_high, prev_high = pivot_highs[-1][1], pivot_highs[-2][1]
    last_low, prev_low = pivot_lows[-1][1], pivot_lows[-2][1]
    higher_high, higher_low = last_high > prev_high, last_low > prev_low
    lower_high, lower_low = last_high < prev_high, last_low < prev_low
    if higher_high and higher_low:
        structure = "up"
    elif lower_high and lower_low:
        structure = "down"
    else:
        structure = None
    return structure, last_high, prev_high, last_low, prev_low


def _fib_retrace(candles: list[dict], close: float):
    """Current retracement of the most recent impulse leg as (fraction, direction).

    fraction: 0.0 = price still at the leg's extreme (no pullback), 1.0 = fully
    retraced to the leg's origin, >1.0 = pushed past the origin (leg likely broken).
    direction: "up" (uptrend pullback, favours BUY) | "down" (favours SELL).
    Returns (None, None) when no clean leg is available or the leg has no range.
    """
    leg = _swing_leg(candles)
    if leg is None:
        return None, None
    leg_high, leg_low, newer_is_high = leg
    span = leg_high - leg_low
    if span <= 0:
        return None, None
    if newer_is_high:
        # Up-leg (low→high); price retraces down from the high toward the low.
        return (leg_high - close) / span, "up"
    # Down-leg (high→low); price retraces up from the low toward the high.
    return (close - leg_low) / span, "down"


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
    structure, struct_last_high, struct_prev_high, struct_last_low, struct_prev_low = \
        _market_structure(candles)
    fib_retrace, fib_leg_dir = _fib_retrace(candles, last["close"])
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
        "ema200": _ema(closes, 200)[-1],
        "rsi": _rsi(closes),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "bb_upper": bb_u,
        "bb_mid": bb_m,
        "bb_lower": bb_l,
        "atr": _atr(candles),
        "adx": _adx(candles),
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "vwap": _vwap_session(candles),
        "swing_high": swing_high,
        "swing_low": swing_low,
        "structure": structure,               # "up" (HH+HL) | "down" (LH+LL) | None
        "struct_last_high": struct_last_high,
        "struct_prev_high": struct_prev_high,
        "struct_last_low": struct_last_low,
        "struct_prev_low": struct_prev_low,
        "fib_retrace": fib_retrace,
        "fib_leg_dir": fib_leg_dir,
    }
