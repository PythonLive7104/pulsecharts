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
BREAKOUT_EPS = 0.001  # within 0.1% of the swing extreme counts as a break
ADX_TREND_MIN = 25    # ADX above this = a trend with enough strength to trade


# 200-EMA trend filter. When True (historical default), non-breakout strategies
# require price on the trend-correct side of the 200 EMA (and the HTF regime check
# in tasks.py demands agreement with the 4h/1d 200 EMA). When False the 200 EMA is
# dropped from every strategy trigger AND the HTF regime check — direction then
# rests on the fast 9/21(/50) EMAs, with the Fib-pullback zone doing the entry
# confirmation instead. Live value set from settings.SIGNAL_EMA200_TREND_FILTER at
# startup (SignalsConfig.ready); backtest flips it with --no-ema200.
EMA200_TREND_FILTER = True


def _side_of_ema200(close, ema200, buy) -> bool:
    """Whether price is on the trend-correct side of the 200 EMA for `buy`. Always
    True when the 200-EMA trend filter is disabled, so strategies fall back to their
    fast-EMA / momentum triggers plus the Fib-zone confirmation."""
    if not EMA200_TREND_FILTER:
        return True
    return close > ema200 if buy else close < ema200


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
    return (_side_of_ema200(close, ema200, True) and hist > 0) or \
           (_side_of_ema200(close, ema200, False) and hist < 0)


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
    up = _side_of_ema200(close, ema200, True) and ema9 > ema21 and rsi >= 50
    down = _side_of_ema200(close, ema200, False) and ema9 < ema21 and rsi <= 50
    return up or down


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


def _trend_pullback(ind: dict) -> bool:
    # Buy the dip / sell the rally inside an established EMA200 trend, while RSI
    # has cooled into a pullback zone rather than running hot.
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "rsi")
    if v is None:
        return False
    close, ema9, ema21, ema200, rsi = v
    up = _side_of_ema200(close, ema200, True) and ema9 > ema21 and 40 <= rsi < 50
    down = _side_of_ema200(close, ema200, False) and ema9 < ema21 and 50 < rsi <= 60
    return up or down


def _ema_ribbon(ind: dict) -> bool:
    # Fully-stacked EMA alignment (9 > 21 > 200, or reversed) with price riding the
    # ribbon — a clean, strong trend. Tangled EMAs (no clear order) don't qualify.
    v = _vals(ind, "close", "ema9", "ema21", "ema200")
    if v is None:
        return False
    close, ema9, ema21, ema200 = v
    up = ema9 > ema21 and close > ema9 and _side_of_ema200(ema21, ema200, True)
    down = ema9 < ema21 and close < ema9 and _side_of_ema200(ema21, ema200, False)
    return up or down


def _donchian_trend(ind: dict) -> bool:
    # Turtle-style channel breakout in the direction of the EMA 200 trend: price
    # pushing the recent swing extreme while aligned with the major trend.
    v = _vals(ind, "close", "swing_high", "swing_low", "ema200")
    if v is None:
        return False
    close, hi, lo, ema200 = v
    up = close >= hi * (1 - BREAKOUT_EPS) and _side_of_ema200(close, ema200, True)
    down = close <= lo * (1 + BREAKOUT_EPS) and _side_of_ema200(close, ema200, False)
    return up or down


def _adx_trend(ind: dict) -> bool:
    # Only trade when ADX confirms a genuinely strong trend; direction comes from
    # the EMA 200 / fast-EMA alignment.
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "adx")
    if v is None:
        return False
    close, ema9, ema21, ema200, adx = v
    if adx < ADX_TREND_MIN:
        return False
    up = _side_of_ema200(close, ema200, True) and ema9 > ema21
    down = _side_of_ema200(close, ema200, False) and ema9 < ema21
    return up or down


PREGATES = {
    "momentum-crossover": _momentum_crossover,
    "macd-trend-following": _macd_trend_following,
    "volatility-breakout": _volatility_breakout,
    "trend-rider": _trend_rider,
    "vwap-trend": _vwap_trend,
    "bollinger-breakout": _bollinger_breakout,
    "trend-pullback": _trend_pullback,
    "ema-ribbon": _ema_ribbon,
    "donchian-trend": _donchian_trend,
    "adx-trend": _adx_trend,
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
    if _side_of_ema200(close, ema200, True) and hist > 0:
        return "BUY"
    if _side_of_ema200(close, ema200, False) and hist < 0:
        return "SELL"
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
    if _side_of_ema200(close, ema200, True) and ema9 > ema21 and rsi >= 50:
        return "BUY"
    if _side_of_ema200(close, ema200, False) and ema9 < ema21 and rsi <= 50:
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


def _dir_trend_pullback(ind: dict) -> str | None:
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "rsi")
    if v is None:
        return None
    close, ema9, ema21, ema200, rsi = v
    if _side_of_ema200(close, ema200, True) and ema9 > ema21 and 40 <= rsi < 50:
        return "BUY"
    if _side_of_ema200(close, ema200, False) and ema9 < ema21 and 50 < rsi <= 60:
        return "SELL"
    return None


def _dir_ema_ribbon(ind: dict) -> str | None:
    v = _vals(ind, "close", "ema9", "ema21", "ema200")
    if v is None:
        return None
    close, ema9, ema21, ema200 = v
    if ema9 > ema21 and close > ema9 and _side_of_ema200(ema21, ema200, True):
        return "BUY"
    if ema9 < ema21 and close < ema9 and _side_of_ema200(ema21, ema200, False):
        return "SELL"
    return None


def _dir_donchian_trend(ind: dict) -> str | None:
    v = _vals(ind, "close", "swing_high", "swing_low", "ema200")
    if v is None:
        return None
    close, hi, lo, ema200 = v
    if close >= hi * (1 - BREAKOUT_EPS) and _side_of_ema200(close, ema200, True):
        return "BUY"
    if close <= lo * (1 + BREAKOUT_EPS) and _side_of_ema200(close, ema200, False):
        return "SELL"
    return None


def _dir_adx_trend(ind: dict) -> str | None:
    v = _vals(ind, "close", "ema9", "ema21", "ema200", "adx")
    if v is None:
        return None
    close, ema9, ema21, ema200, adx = v
    if adx < ADX_TREND_MIN:
        return None
    if _side_of_ema200(close, ema200, True) and ema9 > ema21:
        return "BUY"
    if _side_of_ema200(close, ema200, False) and ema9 < ema21:
        return "SELL"
    return None


DIRECTIONS = {
    "momentum-crossover": _dir_momentum,
    "macd-trend-following": _dir_macd,
    "volatility-breakout": _dir_breakout,
    "trend-rider": _dir_trend_rider,
    "vwap-trend": _dir_vwap,
    "bollinger-breakout": _dir_bollinger_breakout,
    "trend-pullback": _dir_trend_pullback,
    "ema-ribbon": _dir_ema_ribbon,
    "donchian-trend": _dir_donchian_trend,
    "adx-trend": _dir_adx_trend,
}


# Which EMA-alignment gate non-breakout signals must pass. Switchable so the
# trend-strictness vs signal-volume trade-off can be backtested head-to-head
# (backtest --ema-gate ...). The live value is set from settings.SIGNAL_EMA_GATE
# at startup (SignalsConfig.ready); this module default is the backtest winner.
#   "stack200"  : EMA9 > EMA21 > EMA200            full major-trend stack (strictest, fewest signals)
#   "stack50"   : EMA9 > EMA21 > EMA50             full intermediate stack (more signals — chosen default)
#   "filter200" : close > EMA200 AND EMA9 > EMA21  major-trend filter (tested worst)
EMA_GATE_MODE = "stack50"


# Overextension guard (A): block NEW non-breakout entries once price has stretched
# more than this many ATRs beyond EMA21. In a parabolic blow-off (e.g. a runaway
# USDJPY leg) the trend gates are MORE satisfied the more extended price gets, so
# without this the engine keeps re-issuing BUYs straight into the top. Distance is
# measured as (close - EMA21) / ATR. 0 disables. The live value is set from
# settings.SIGNAL_OVEREXT_ATR_MULT at startup (SignalsConfig.ready).
OVEREXT_ATR_MULT = 0.0


def is_overextended(ind: dict, direction: str) -> bool:
    """True if price has stretched more than OVEREXT_ATR_MULT × ATR beyond EMA21 in
    the trade's direction — a chase entry into an extended move. Returns False when
    the guard is disabled (mult <= 0) or a needed indicator is missing."""
    mult = OVEREXT_ATR_MULT
    if mult <= 0:
        return False
    v = _vals(ind, "close", "ema21", "atr")
    if v is None:
        return False
    close, ema21, atr = v
    if atr <= 0:
        return False
    stretch = (close - ema21) / atr  # +ve = above the mean, -ve = below
    if direction == "BUY":
        return stretch > mult
    if direction == "SELL":
        return -stretch > mult
    return False


# Overbought/oversold cap (B): the distance guard (A) measures stretch from EMA21,
# which shrinks on a sustained staircase climb once the EMA catches up — so it can't
# see a "buy at RSI 72 near the high" chase. RSI does. Reject BUY above RSI_OVERBOUGHT
# and SELL below RSI_OVERSOLD. 0 disables a bound. Live values set from
# settings.SIGNAL_RSI_OVERBOUGHT / _OVERSOLD at startup (SignalsConfig.ready).
RSI_OVERBOUGHT = 0.0
RSI_OVERSOLD = 0.0


def is_rsi_extreme(ind: dict, direction: str) -> bool:
    """True if entering `direction` would mean buying into overbought RSI or selling
    into oversold RSI. Returns False if the relevant bound is disabled (0) or RSI is
    missing."""
    rsi = ind.get("rsi")
    if rsi is None:
        return False
    rsi = float(rsi)
    if direction == "BUY":
        return RSI_OVERBOUGHT > 0 and rsi > RSI_OVERBOUGHT
    if direction == "SELL":
        return RSI_OVERSOLD > 0 and rsi < RSI_OVERSOLD
    return False


# Fib-pullback gate (D): only allow a NEW non-breakout entry once price has retraced
# into the [MIN, MAX] band of the most recent impulse leg (fractal-pivot swing) — buy
# the dip / sell the rally instead of chasing an extended move that then snaps back
# into the stop. MIN <= 0 disables the gate. 0.5–0.786 is the classic continuation
# zone: below MIN the pullback is too shallow (still chasing), above MAX the swing has
# retraced far enough that it's likely breaking (reversal, not a pullback). Live values
# set from settings.SIGNAL_FIB_PULLBACK_MIN / _MAX at startup (SignalsConfig.ready).
FIB_PULLBACK_MIN = 0.0
FIB_PULLBACK_MAX = 0.786


def is_in_fib_zone(ind: dict, direction: str) -> bool:
    """True if price sits inside the pullback band of the most recent impulse leg on
    the side matching `direction`. Returns True when the gate is disabled (MIN <= 0).
    When the gate is ENABLED but no valid leg/retracement is available, returns False —
    no confirmed pullback structure means no trade (fail-closed, deliberately)."""
    lo = FIB_PULLBACK_MIN
    if lo <= 0:
        return True  # gate disabled
    retrace = ind.get("fib_retrace")
    leg_dir = ind.get("fib_leg_dir")
    if retrace is None or leg_dir is None:
        return False
    want = "up" if direction == "BUY" else "down" if direction == "SELL" else None
    if want is None or leg_dir != want:
        return False
    return lo <= retrace <= FIB_PULLBACK_MAX


def ema_trend_aligned(ind: dict, direction: str) -> bool:
    """Whether `direction` agrees with the EMA structure under EMA_GATE_MODE. No
    non-breakout strategy may emit a signal unless this passes. Returns False if a
    needed EMA is missing."""
    buy = direction == "BUY"
    if direction not in ("BUY", "SELL"):
        return False

    if EMA_GATE_MODE == "stack50":
        v = _vals(ind, "ema9", "ema21", "ema50")
        if v is None:
            return False
        ema9, ema21, ema50 = v
        return ema9 > ema21 > ema50 if buy else ema9 < ema21 < ema50

    if EMA_GATE_MODE == "filter200":
        v = _vals(ind, "close", "ema9", "ema21", "ema200")
        if v is None:
            return False
        close, ema9, ema21, ema200 = v
        return (close > ema200 and ema9 > ema21) if buy else (close < ema200 and ema9 < ema21)

    # default: "stack200" — full major-trend stack
    v = _vals(ind, "ema9", "ema21", "ema200")
    if v is None:
        return False
    ema9, ema21, ema200 = v
    return ema9 > ema21 > ema200 if buy else ema9 < ema21 < ema200


# Breakout strategies trade range expansions and legitimately fire BEFORE the EMA
# stack lines up — gating them on the full trend stack defeats their purpose (and
# they backtest negative under it). They're exempt from the EMA-stack gate; every
# trend/momentum strategy still requires the full stack.
EMA_STACK_EXEMPT = {"bollinger-breakout", "volatility-breakout"}


def passes_ema_gate(strategy_slug: str, indicators: dict, direction: str) -> bool:
    """Whether `direction` is allowed for this strategy. Breakout strategies are
    exempt; every other strategy must be fully stacked behind the direction."""
    if strategy_slug in EMA_STACK_EXEMPT:
        return True
    return ema_trend_aligned(indicators, direction)


def passes_overext_gate(strategy_slug: str, indicators: dict, direction: str) -> bool:
    """Whether `direction` is allowed given how far price has run from the mean.
    Breakout strategies are exempt — extension is the whole premise of a breakout —
    so the guard only restrains trend/momentum strategies from chasing a blow-off."""
    if strategy_slug in EMA_STACK_EXEMPT:
        return True
    return not is_overextended(indicators, direction)


def passes_rsi_gate(strategy_slug: str, indicators: dict, direction: str) -> bool:
    """Whether `direction` is allowed given RSI extremes — no buying into overbought
    or selling into oversold. Breakout strategies are exempt (they fire on momentum
    thrusts that legitimately push RSI to an extreme)."""
    if strategy_slug in EMA_STACK_EXEMPT:
        return True
    return not is_rsi_extreme(indicators, direction)


def passes_fib_gate(strategy_slug: str, indicators: dict, direction: str) -> bool:
    """Whether `direction` is allowed given the Fib-pullback requirement — only enter
    after a retracement into the zone, never chasing an extended move. Applies to
    EVERY strategy (breakouts included): with the 200-EMA trend filter off, the Fib
    zone is the mandatory entry confirmation, so it is deliberately NOT exempted.
    (No-op anyway when the gate is disabled — is_in_fib_zone returns True.)"""
    return is_in_fib_zone(indicators, direction)


def candidate_direction(strategy_slug: str, indicators: dict) -> str | None:
    """Cheap directional bias ("BUY"/"SELL"/None) implied by the indicators for a
    strategy — no LLM. Used to detect a trend flip against an open signal.

    Non-breakout strategies are filtered through the full 9/21/200 EMA stack, so a
    trend/momentum signal is never emitted against the stack. Breakout strategies
    (EMA_STACK_EXEMPT) keep their own logic — see passes_ema_gate."""
    fn = DIRECTIONS.get(strategy_slug)
    if fn is None:
        return None
    direction = fn(indicators)
    if direction and not passes_ema_gate(strategy_slug, indicators, direction):
        return None
    if direction and not passes_overext_gate(strategy_slug, indicators, direction):
        return None
    if direction and not passes_rsi_gate(strategy_slug, indicators, direction):
        return None
    if direction and not passes_fib_gate(strategy_slug, indicators, direction):
        return None
    return direction


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
    macd_ok = hist is not None and (hist > 0) == buy
    # Primary trend/location confirmation. With the 200-EMA filter ON this is
    # "price on the right side of the 200 EMA" (major trend); with it OFF the 200
    # EMA is no longer part of the setup, so the Fib-pullback zone is the
    # confirmation instead — keeping the score on the same scale so signals don't
    # silently fall under the delivery floor just because the anchor changed.
    if EMA200_TREND_FILTER:
        trend_ok = close is not None and ema200 is not None and (close > ema200) == buy
    else:
        trend_ok = is_in_fib_zone(ind, direction)

    if ema_ok:
        score += 8
    if trend_ok:
        score += 8   # major-trend (200 EMA) or Fib-zone confirmation
    if macd_ok:
        score += 7
    if rsi is not None and (rsi >= 50) == buy:
        score += min(8.0, abs(rsi - 50) / 50 * 16)   # further from 50 = stronger
    if vol is not None and vol_ma not in (None, 0) and vol > vol_ma:
        score += 5
    if ema_ok and trend_ok and macd_ok:
        score += 4   # full trend confluence bonus

    return int(max(50, min(95, round(score))))
