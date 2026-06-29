"""Deterministic stop-loss / take-profit / dollar math (Section 19.2).

The LLM provides the *judgment* (direction, confidence, reasoning); these
formulas turn entry + ATR + swing levels into the full signal card. Computing
this in Python (rather than asking the LLM) keeps the ~20 interdependent figures
exact and internally consistent.
"""

from __future__ import annotations

# Risk-multiple per take-profit level — three clean targets at 1R / 2R / 3R.
# TP4 was removed: 3R is the proven reachable ceiling (~45% of winners run to it in
# backtests), while the old 4.5R runner almost never filled. Even 1R/2R/3R spacing
# is the intuitive ladder; pairs with the tight local-pivot stop so 3R is a modest
# % move. (tp4 / *_tp4 fields are kept on the model but set None — see compute_levels.)
TP_MULTIPLES = {1: 1.0, 2: 2.0, 3: 3.0}
# 2.0 (was 1.5): a tighter stop gets wicked out by routine noise before the setup
# can resolve — the main driver of the early loss rate. Wider stop = fewer
# premature stop-outs (TPs scale with it, so the risk:reward ratios are unchanged).
ATR_STOP_MULT = 2.0
# Cap on how far a swing extreme can widen the stop. The swing levels are 50-bar
# ABSOLUTE extremes (not local pivots), so on a trend-continuation entry — where
# price has already pulled away from its recent high/low — the swing stop lands
# far from entry and produces an absurd risk distance (e.g. an 8.8% stop on gold,
# a 3.5% stop on an FX major where ~0.5% is normal). Bound the stop to at most
# MAX_ATR_MULT × ATR so a distant extreme can refine the stop but never blow it out.
MAX_ATR_MULT = 3.0
# Place the swing-based stop just *beyond* the swing level, so an exact-wick touch
# of the prior high/low doesn't stop us out.
SWING_BUFFER = 0.0015  # 0.15%
TRADE_SIZE = 100.0  # illustrative notional for the dollar figures (Section 19.2)


def compute_levels(
    direction: str,
    entry: float,
    atr: float,
    swing_high: float,
    swing_low: float,
) -> dict | None:
    """Return SL / TP1–4 / risk-reward / dollar fields, or None if no valid stop.

    The stop sits at least ATR_STOP_MULT×ATR beyond entry (past routine noise),
    is widened to the swing extreme when that's further, but is clamped to at most
    MAX_ATR_MULT×ATR so a distant 50-bar extreme can't produce an absurd stop:

    BUY  SL = clamp(swing_low·(1-buffer),  entry - 3.0·ATR, entry - 2.0·ATR)
    SELL SL = clamp(swing_high·(1+buffer), entry + 2.0·ATR, entry + 3.0·ATR)
    risk_distance = |entry - SL|;  TPn = entry ± multiple·risk_distance.
    """
    atr_floor = ATR_STOP_MULT * atr  # minimum stop distance (beyond routine noise)
    atr_cap = MAX_ATR_MULT * atr     # maximum stop distance (don't let a far swing blow it out)
    if direction == "BUY":
        swing_stop = swing_low * (1 - SWING_BUFFER)
        # widen toward the swing if it's beyond the floor, but never past the cap
        stop_loss = max(min(swing_stop, entry - atr_floor), entry - atr_cap)
        sign = 1
    elif direction == "SELL":
        swing_stop = swing_high * (1 + SWING_BUFFER)
        stop_loss = min(max(swing_stop, entry + atr_floor), entry + atr_cap)
        sign = -1
    else:
        return None

    risk = abs(entry - stop_loss)
    if risk <= 0 or entry <= 0:
        return None  # degenerate setup — no tradeable stop

    tp = {n: entry + sign * mult * risk for n, mult in TP_MULTIPLES.items()}
    risk_pct = risk / entry * 100
    reward_pct = {n: abs(tp[n] - entry) / entry * 100 for n in TP_MULTIPLES}
    rr = {n: abs(tp[n] - entry) / risk for n in TP_MULTIPLES}  # == the multiple

    return {
        "stop_loss": stop_loss,
        "tp1": tp[1], "tp2": tp[2], "tp3": tp[3], "tp4": None,
        "risk_pct": risk_pct,
        "reward_tp1_pct": reward_pct[1], "reward_tp2_pct": reward_pct[2],
        "reward_tp3_pct": reward_pct[3], "reward_tp4_pct": None,
        "risk_reward_tp1": rr[1], "risk_reward_tp2": rr[2],
        "risk_reward_tp3": rr[3], "risk_reward_tp4": None,
        "dollar_risk": risk_pct / 100 * TRADE_SIZE,
        "dollar_tp1": reward_pct[1] / 100 * TRADE_SIZE,
        "dollar_tp2": reward_pct[2] / 100 * TRADE_SIZE,
        "dollar_tp3": reward_pct[3] / 100 * TRADE_SIZE,
        "dollar_tp4": None,
    }
