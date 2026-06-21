"""Deterministic stop-loss / take-profit / dollar math (Section 19.2).

The LLM provides the *judgment* (direction, confidence, reasoning); these
formulas turn entry + ATR + swing levels into the full signal card. Computing
this in Python (rather than asking the LLM) keeps the ~20 interdependent figures
exact and internally consistent.
"""

from __future__ import annotations

# Risk-multiple per take-profit level (Section 19.2): 1:1, 1:2, 1:3, ~1:4.5.
TP_MULTIPLES = {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.5}
# 2.0 (was 1.5): a tighter stop gets wicked out by routine noise before the setup
# can resolve — the main driver of the early loss rate. Wider stop = fewer
# premature stop-outs (TPs scale with it, so the risk:reward ratios are unchanged).
ATR_STOP_MULT = 2.0
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

    BUY  SL = min(swing_low·(1-buffer),  entry - 2.0·ATR)
    SELL SL = max(swing_high·(1+buffer), entry + 2.0·ATR)
    risk_distance = |entry - SL|;  TPn = entry ± multiple·risk_distance.
    """
    if direction == "BUY":
        stop_loss = min(swing_low * (1 - SWING_BUFFER), entry - ATR_STOP_MULT * atr)
        sign = 1
    elif direction == "SELL":
        stop_loss = max(swing_high * (1 + SWING_BUFFER), entry + ATR_STOP_MULT * atr)
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
        "tp1": tp[1], "tp2": tp[2], "tp3": tp[3], "tp4": tp[4],
        "risk_pct": risk_pct,
        "reward_tp1_pct": reward_pct[1], "reward_tp2_pct": reward_pct[2],
        "reward_tp3_pct": reward_pct[3], "reward_tp4_pct": reward_pct[4],
        "risk_reward_tp1": rr[1], "risk_reward_tp2": rr[2],
        "risk_reward_tp3": rr[3], "risk_reward_tp4": rr[4],
        "dollar_risk": risk_pct / 100 * TRADE_SIZE,
        "dollar_tp1": reward_pct[1] / 100 * TRADE_SIZE,
        "dollar_tp2": reward_pct[2] / 100 * TRADE_SIZE,
        "dollar_tp3": reward_pct[3] / 100 * TRADE_SIZE,
        "dollar_tp4": reward_pct[4] / 100 * TRADE_SIZE,
    }
