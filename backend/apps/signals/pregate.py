"""Rule-based pre-gate (cost control for the signal engine).

Runs on the already-computed indicator snapshot (free — no LLM) and decides
whether a setup is even *plausible* for a given strategy. If the basic
conditions clearly aren't present, we skip the (paid) LLM call entirely. The LLM
still makes the final judgment for setups that pass — the gate only filters out
obvious non-setups, which on most candles is the large majority.

Each gate is a loose *necessary* condition, deliberately not the full strategy,
so it doesn't pre-empt the model's judgment — it just avoids paying to ask about
candles where nothing is happening.

Keyed by SignalService.slug. Unknown slugs default to "ask the LLM" (no gating).
"""

from __future__ import annotations

# Tunables.
RSI_OVERBOUGHT = 65
RSI_OVERSOLD = 35
BREAKOUT_EPS = 0.001  # within 0.1% of the swing extreme counts as a break
STOCH_OVERSOLD = 25
STOCH_OVERBOUGHT = 75


def _vals(ind: dict, *keys):
    """Return the requested indicator values, or None if any is missing."""
    out = []
    for k in keys:
        v = ind.get(k)
        if v is None:
            return None
        out.append(float(v))
    return out


def _momentum_crossover(ind: dict) -> bool:
    v = _vals(ind, "ema9", "ema21", "macd_hist", "rsi")
    if v is None:
        return False
    ema9, ema21, hist, rsi = v
    up = ema9 > ema21 and hist > 0 and rsi >= 50
    down = ema9 < ema21 and hist < 0 and rsi <= 50
    return up or down


def _macd_trend_following(ind: dict) -> bool:
    v = _vals(ind, "close", "ema200", "macd_hist")
    if v is None:
        return False
    close, ema200, hist = v
    return (close > ema200 and hist > 0) or (close < ema200 and hist < 0)


def _bollinger_mean_reversion(ind: dict) -> bool:
    v = _vals(ind, "close", "bb_upper", "bb_lower", "rsi")
    if v is None:
        return False
    close, upper, lower, rsi = v
    return (close >= upper and rsi >= RSI_OVERBOUGHT) or (close <= lower and rsi <= RSI_OVERSOLD)


def _volatility_breakout(ind: dict) -> bool:
    v = _vals(ind, "close", "swing_high", "swing_low", "volume", "volume_ma20")
    if v is None:
        return False
    close, hi, lo, vol, vol_ma = v
    if vol_ma <= 0 or vol <= vol_ma:  # need above-average volume
        return False
    return close >= hi * (1 - BREAKOUT_EPS) or close <= lo * (1 + BREAKOUT_EPS)


def _trend_rider(ind: dict) -> bool:
    # Trend-following with momentum confirmation: price on the right side of the
    # EMA200 trend, fast EMAs aligned, RSI confirming.
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "rsi")
    if v is None:
        return False
    close, ema9, ema21, ema200, rsi = v
    up = close > ema200 and ema9 > ema21 and rsi >= 50
    down = close < ema200 and ema9 < ema21 and rsi <= 50
    return up or down


def _stochastic_reversal(ind: dict) -> bool:
    # Stochastic %K/%D crossover out of an extreme zone — earlier reversal signal
    # than RSI in range-bound markets.
    v = _vals(ind, "stoch_k", "stoch_d")
    if v is None:
        return False
    k, d = v
    bull = k <= STOCH_OVERSOLD and k > d       # turning up from oversold
    bear = k >= STOCH_OVERBOUGHT and k < d     # turning down from overbought
    return bull or bear


def _vwap_trend(ind: dict) -> bool:
    # Price holding above/below the session VWAP with momentum agreeing — VWAP as
    # the intraday trend / support-resistance line.
    v = _vals(ind, "close", "vwap", "rsi")
    if v is None:
        return False
    close, vwap, rsi = v
    return (close > vwap and rsi >= 50) or (close < vwap and rsi <= 50)


def _bollinger_breakout(ind: dict) -> bool:
    # Band "ride": a close beyond a band on expanding volume signals breakout
    # continuation (the opposite stance to mean reversion).
    v = _vals(ind, "close", "bb_upper", "bb_lower", "rsi", "volume", "volume_ma20")
    if v is None:
        return False
    close, upper, lower, rsi, vol, vol_ma = v
    if vol_ma <= 0 or vol <= vol_ma:
        return False
    return (close >= upper and rsi >= 55) or (close <= lower and rsi <= 45)


def _vwap_reversion(ind: dict) -> bool:
    # Price stretched at least 1×ATR away from VWAP with RSI extreme — snap back
    # toward the volume-weighted mean.
    v = _vals(ind, "close", "vwap", "atr", "rsi")
    if v is None:
        return False
    close, vwap, atr, rsi = v
    if atr <= 0 or abs(close - vwap) < atr:
        return False
    return (close < vwap and rsi <= 40) or (close > vwap and rsi >= 60)


def _trend_pullback(ind: dict) -> bool:
    # Buy the dip / sell the rally inside an established EMA200 trend, while RSI
    # has cooled into a pullback zone rather than running hot.
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "rsi")
    if v is None:
        return False
    close, ema9, ema21, ema200, rsi = v
    up = close > ema200 and ema9 > ema21 and 40 <= rsi < 50
    down = close < ema200 and ema9 < ema21 and 50 < rsi <= 60
    return up or down


PREGATES = {
    "momentum-crossover": _momentum_crossover,
    "macd-trend-following": _macd_trend_following,
    "bollinger-mean-reversion": _bollinger_mean_reversion,
    "volatility-breakout": _volatility_breakout,
    "trend-rider": _trend_rider,
    "stochastic-reversal": _stochastic_reversal,
    "vwap-trend": _vwap_trend,
    "bollinger-breakout": _bollinger_breakout,
    "vwap-reversion": _vwap_reversion,
    "trend-pullback": _trend_pullback,
}


def passes_pregate(strategy_slug: str, indicators: dict) -> bool:
    """True if it's worth asking the LLM about this (symbol, strategy) snapshot."""
    gate = PREGATES.get(strategy_slug)
    if gate is None:
        return True  # unknown strategy — don't gate
    return gate(indicators)


# --- cheap directional bias (no LLM) ---
# Used to decide whether the trend has flipped versus an already-open signal, so
# we only allow a fresh call for a strategy when its direction actually changes.


def _dir_momentum(ind: dict) -> str | None:
    v = _vals(ind, "ema9", "ema21", "macd_hist", "rsi")
    if v is None:
        return None
    ema9, ema21, hist, rsi = v
    if ema9 > ema21 and hist > 0 and rsi >= 50:
        return "BUY"
    if ema9 < ema21 and hist < 0 and rsi <= 50:
        return "SELL"
    return None


def _dir_macd(ind: dict) -> str | None:
    v = _vals(ind, "close", "ema200", "macd_hist")
    if v is None:
        return None
    close, ema200, hist = v
    if close > ema200 and hist > 0:
        return "BUY"
    if close < ema200 and hist < 0:
        return "SELL"
    return None


def _dir_bollinger(ind: dict) -> str | None:
    # Mean reversion: a tag of the upper band is a SELL setup, lower band a BUY.
    v = _vals(ind, "close", "bb_upper", "bb_lower", "rsi")
    if v is None:
        return None
    close, upper, lower, rsi = v
    if close >= upper and rsi >= RSI_OVERBOUGHT:
        return "SELL"
    if close <= lower and rsi <= RSI_OVERSOLD:
        return "BUY"
    return None


def _dir_breakout(ind: dict) -> str | None:
    v = _vals(ind, "close", "swing_high", "swing_low", "volume", "volume_ma20")
    if v is None:
        return None
    close, hi, lo, vol, vol_ma = v
    if vol_ma <= 0 or vol <= vol_ma:
        return None
    if close >= hi * (1 - BREAKOUT_EPS):
        return "BUY"
    if close <= lo * (1 + BREAKOUT_EPS):
        return "SELL"
    return None


def _dir_trend_rider(ind: dict) -> str | None:
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "rsi")
    if v is None:
        return None
    close, ema9, ema21, ema200, rsi = v
    if close > ema200 and ema9 > ema21 and rsi >= 50:
        return "BUY"
    if close < ema200 and ema9 < ema21 and rsi <= 50:
        return "SELL"
    return None


def _dir_stochastic(ind: dict) -> str | None:
    v = _vals(ind, "stoch_k", "stoch_d")
    if v is None:
        return None
    k, d = v
    if k <= STOCH_OVERSOLD and k > d:
        return "BUY"
    if k >= STOCH_OVERBOUGHT and k < d:
        return "SELL"
    return None


def _dir_vwap(ind: dict) -> str | None:
    v = _vals(ind, "close", "vwap", "rsi")
    if v is None:
        return None
    close, vwap, rsi = v
    if close > vwap and rsi >= 50:
        return "BUY"
    if close < vwap and rsi <= 50:
        return "SELL"
    return None


def _dir_bollinger_breakout(ind: dict) -> str | None:
    v = _vals(ind, "close", "bb_upper", "bb_lower", "rsi", "volume", "volume_ma20")
    if v is None:
        return None
    close, upper, lower, rsi, vol, vol_ma = v
    if vol_ma <= 0 or vol <= vol_ma:
        return None
    if close >= upper and rsi >= 55:
        return "BUY"
    if close <= lower and rsi <= 45:
        return "SELL"
    return None


def _dir_vwap_reversion(ind: dict) -> str | None:
    v = _vals(ind, "close", "vwap", "atr", "rsi")
    if v is None:
        return None
    close, vwap, atr, rsi = v
    if atr <= 0 or abs(close - vwap) < atr:
        return None
    if close < vwap and rsi <= 40:
        return "BUY"   # stretched below VWAP — expect a bounce up
    if close > vwap and rsi >= 60:
        return "SELL"  # stretched above VWAP — expect a fade down
    return None


def _dir_trend_pullback(ind: dict) -> str | None:
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "rsi")
    if v is None:
        return None
    close, ema9, ema21, ema200, rsi = v
    if close > ema200 and ema9 > ema21 and 40 <= rsi < 50:
        return "BUY"
    if close < ema200 and ema9 < ema21 and 50 < rsi <= 60:
        return "SELL"
    return None


DIRECTIONS = {
    "momentum-crossover": _dir_momentum,
    "macd-trend-following": _dir_macd,
    "bollinger-mean-reversion": _dir_bollinger,
    "volatility-breakout": _dir_breakout,
    "trend-rider": _dir_trend_rider,
    "stochastic-reversal": _dir_stochastic,
    "vwap-trend": _dir_vwap,
    "bollinger-breakout": _dir_bollinger_breakout,
    "vwap-reversion": _dir_vwap_reversion,
    "trend-pullback": _dir_trend_pullback,
}


def candidate_direction(strategy_slug: str, indicators: dict) -> str | None:
    """Cheap directional bias ("BUY"/"SELL"/None) implied by the indicators for a
    strategy — no LLM. Used to detect a trend flip against an open signal."""
    fn = DIRECTIONS.get(strategy_slug)
    if fn is None:
        return None
    return fn(indicators)


def confidence_score(direction: str, ind: dict) -> int:
    """Deterministic *conviction* score (~55–95): how strongly the indicators
    line up behind `direction`. This is NOT a win-rate / accuracy figure — it just
    measures how many confirmations agree. Realized accuracy is tracked separately
    (stats.accuracy_stats). Varies per setup so it's a meaningful curation signal.
    """
    if direction not in ("BUY", "SELL"):
        return 0
    buy = direction == "BUY"

    def f(k):
        v = ind.get(k)
        return float(v) if v is not None else None

    close, ema9, ema21, ema200 = f("close"), f("ema9"), f("ema21"), f("ema200")
    rsi, hist, vol, vol_ma = f("rsi"), f("macd_hist"), f("volume"), f("volume_ma20")

    score = 55.0
    ema_ok = ema9 is not None and ema21 is not None and (ema9 > ema21) == buy
    trend_ok = close is not None and ema200 is not None and (close > ema200) == buy
    macd_ok = hist is not None and (hist > 0) == buy

    if ema_ok:
        score += 8
    if trend_ok:
        score += 8   # price vs the 200 EMA (major trend)
    if macd_ok:
        score += 7
    if rsi is not None and (rsi >= 50) == buy:
        score += min(8.0, abs(rsi - 50) / 50 * 16)   # further from 50 = stronger
    if vol is not None and vol_ma not in (None, 0) and vol > vol_ma:
        score += 5
    if ema_ok and trend_ok and macd_ok:
        score += 4   # full trend confluence bonus

    return int(max(50, min(95, round(score))))
