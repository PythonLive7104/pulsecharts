"""Signal outcome evaluation (Section 13.7, 18, 20.5).

Walks the candles that came *after* a signal was generated and decides what
happened: did price hit a take-profit before the stop-loss, and how far did it
run? This is what lets us track realized accuracy honestly before making any
"high percentage" claims.

Intrabar ordering: when a single candle's range spans both the stop and a
take-profit, we can't know from OHLC alone which was touched first. We break the
tie by the candle's **open** — whichever level (the nearest TP, tp1, or the stop)
sits closer to the open is assumed reached first. This is a fairer heuristic than
blanket worst-case (always-stop), which structurally inflated the loss rate on
noisy candles. If the open is unavailable we fall back to worst-case (stop first).
"""

from __future__ import annotations


def walk(direction: str, entry, stop_loss, tps, candles, breakeven_after_tp1: bool = False) -> dict:
    """Walk candles (oldest→newest, each {high, low, open?}) and return:

        {best_tp: 0-3, stopped: bool, terminal: bool, be_stopped: bool, mfe_pct, mae_pct}

    - best_tp: highest TP level reached (0 = none)
    - stopped: the (effective) stop-loss was touched at some point
    - terminal: the trade is definitively over (stop touched, or the last TP reached)
    - be_stopped: the runner was closed by the breakeven stop *after* banking TP1
      (only ever True when ``breakeven_after_tp1`` is set)
    - mfe_pct / mae_pct: max favorable / adverse excursion from entry, in %

    When ``breakeven_after_tp1`` is set (the live "let winners run" model, §19.2),
    the stop is raised to the entry price once TP1 is banked. This lets a winner
    keep running toward TP2/TP3 while guaranteeing the remainder can't turn into a
    loss — a reversal after TP1 closes flat at breakeven (best_tp stays ≥ 1, so the
    call is still recorded as a TP1 win, not an SL). Callers that want the raw
    "original stop, TP1 is a permanent win floor" behaviour leave it False.
    """
    buy = direction == "BUY"
    eff_stop = stop_loss  # raised to breakeven once TP1 is banked (if enabled)
    best_tp = 0
    stopped = False
    terminal = False
    be_stopped = False
    mfe = 0.0
    mae = 0.0
    bars = 0  # candles consumed until resolution (or all, if never terminal)

    for c in candles:
        bars += 1
        hi, lo = c["high"], c["low"]
        # Excursions from entry (favorable = in the signal's direction).
        if buy:
            fav = (hi - entry) / entry * 100
            adv = (lo - entry) / entry * 100
        else:
            fav = (entry - lo) / entry * 100
            adv = (entry - hi) / entry * 100
        mfe = max(mfe, fav)
        mae = min(mae, adv)

        # Once TP1 is banked, trail the stop up to breakeven for the runner. Applied
        # at the top of the candle *after* TP1 was reached — never mid-candle on the
        # bar that fills TP1 (you can't move the stop before the fill happens).
        if breakeven_after_tp1 and best_tp >= 1:
            eff_stop = max(eff_stop, entry) if buy else min(eff_stop, entry)

        sl_hit = lo <= eff_stop if buy else hi >= eff_stop
        # Highest TP level whose price was reached this candle (0 = none).
        candle_tp = 0
        for i, tp in enumerate(tps, start=1):
            reached = hi >= tp if buy else lo <= tp
            if reached:
                candle_tp = i

        if sl_hit and candle_tp:
            # Both touched in one candle — resolve the order via the open price.
            opn = c.get("open")
            if opn is None:
                stop_first = True  # worst-case fallback (no open available)
            else:
                # tp1 is the nearest profit target; whichever is closer to the
                # open is assumed hit first.
                stop_first = abs(opn - eff_stop) <= abs(opn - tps[0])
            if stop_first:
                stopped = True
                be_stopped = best_tp >= 1
                terminal = True
                break
            # TP reached first — it's a win regardless of the later stop touch.
            best_tp = max(best_tp, candle_tp)
            if best_tp == len(tps):
                terminal = True
                break
            continue

        # Clean stop (no TP this candle): worst-case ordering still applies.
        if sl_hit:
            stopped = True
            be_stopped = best_tp >= 1
            terminal = True
            break

        if candle_tp:
            best_tp = max(best_tp, candle_tp)
            if best_tp == len(tps):
                terminal = True
                break

    return {
        "best_tp": best_tp,
        "stopped": stopped,
        "terminal": terminal,
        "be_stopped": be_stopped,
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "bars": bars,
    }


def outcome_label(result: dict) -> str | None:
    """Map a walk() result to a Signal.Outcome, or None if still inconclusive.

    None means: no TP and no stop seen yet within the candles provided — the
    caller decides whether to keep waiting or mark EXPIRED.
    """
    if result["best_tp"] > 0:
        return f"TP{result['best_tp']}"  # reached a profit target (even if later stopped)
    if result["stopped"]:
        return "SL"
    return None
