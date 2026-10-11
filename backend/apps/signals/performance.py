"""Capital-curve engine — what the strategy did to MONEY (newPRD §6, §7, §11, §16).

Everything else in this app measures a trade: did it reach TP1, what was its R.
That is not the product question. The product question is the one §2 of the PRD
puts first — "if a user followed this exact strategy with the documented risk,
sizing and cost assumptions, how would their capital have behaved over hundreds
of trades?" — and a win rate cannot answer it. A 70%-win strategy that risks 1R
to make 0.3R bleeds; a 40%-win strategy with 3R winners compounds. This module is
where a stream of R outcomes becomes an equity curve, a drawdown, a profit factor
and an annualized return.

Deliberately dependency-free: no Django, no ORM, no pandas. It takes a list of
``Trade``s and returns plain dicts. That keeps one implementation of these metrics
for both callers that matter — ``manage.py backtest`` (historical and
out-of-sample) and the live ledger of delivered ``Signal`` rows — so the public
performance page can never disagree with the research that justified it. The
PRD's rule that backtested, out-of-sample, paper and live figures are never
blended into one number is the CALLER's job: run this once per segment and label
each result.

Position sizing is fixed-fractional on REALIZED equity, which is the model the
signal cards already imply (risk a fixed % per trade, stop defines 1R):

  * risk at entry = risk_pct × equity closed-and-banked at that moment.
    Open positions' unrealized P&L is NOT counted — a trader cannot size off
    profit that hasn't been booked, and counting it would let one running winner
    inflate the size of every trade opened beside it.
  * ``compounding=False`` sizes every trade off the starting capital instead, which
    isolates strategy edge from the compounding math.
  * Trades overlap. Sizing each one independently means N concurrent positions put
    N × risk_pct at risk simultaneously, so ``max_concurrent`` and ``exposure_pct``
    are reported alongside the curve — a 1%-risk curve built from 12 overlapping
    trades is really a 12%-risk curve, and that has to be visible rather than
    inferred.

A note on the word "win". ``stats.py`` defines a win as reaching TP1 before the
stop, which is the right definition for a signal's outcome. Here a trade is
profitable or not by its realized R NET OF COSTS, so a scratch that only paid the
spread lands in the loss column. The two numbers are both honest and both needed;
they are named differently (``profitable_rate`` here, ``win_rate`` there) so they
can never be mistaken for each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

# Calendar year including leap years — used for CAGR and for annualizing.
YEAR_SECONDS = 365.25 * 24 * 3600
DAY_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Trade:
    """One closed trade, already scored in R.

    ``r`` is realized R net of costs under the exit model the trade was actually
    managed with — this module never re-derives it, because the one definition of
    what a trade earned lives in ``stats.trade_r`` (live) and the backtest's
    per-model accounting. ``opened`` is when risk was committed (so sizing happens
    there) and ``closed`` is when the position went flat (so P&L lands there).
    """

    opened: int
    closed: int
    r: float
    symbol: str = ""
    strategy: str = ""

    def __post_init__(self):
        if self.closed < self.opened:
            raise ValueError(f"trade closed ({self.closed}) before it opened ({self.opened})")


# --------------------------------------------------------------------------
# trade-level metrics (R space — sizing-independent)
# --------------------------------------------------------------------------

def trade_metrics(trades) -> dict:
    """Per-trade economics in R, independent of capital or position size.

    R space is where strategies are compared: it is unaffected by starting
    capital, risk per trade and compounding, so two strategies measured here
    differ only in edge. ``profit_factor`` is None when there were no losses at
    all (the ratio is infinite, and printing a number there would invent one).
    """
    rs = [t.r for t in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    flats = [r for r in rs if r == 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # positive magnitude

    streak_w = streak_l = cur_w = cur_l = 0
    for r in rs:
        if r > 0:
            cur_w, cur_l = cur_w + 1, 0
        elif r < 0:
            cur_w, cur_l = 0, cur_l + 1
        else:  # a flat scratch breaks both streaks without joining either
            cur_w = cur_l = 0
        streak_w, streak_l = max(streak_w, cur_w), max(streak_l, cur_l)

    # Drawdown of the cumulative-R curve: the worst peak-to-trough run of the
    # strategy's own edge, before sizing turns it into money. Reported because it
    # is the figure that survives a change of risk_pct.
    cum = peak = 0.0
    max_r_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_r_dd = max(max_r_dd, peak - cum)

    return {
        "trades": n,
        "profitable": len(wins),
        "unprofitable": len(losses),
        "flat": len(flats),
        "profitable_rate": round(len(wins) / n * 100, 1) if n else None,
        "gross_profit_r": round(gross_profit, 3),
        "gross_loss_r": round(gross_loss, 3),
        "net_r": round(sum(rs), 3),
        # Gross profit / gross loss. Below 1.0 the strategy loses money however it
        # is sized; this is the single most load-bearing number in the module.
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "expectancy_r": round(sum(rs) / n, 4) if n else None,
        "median_r": round(_median(rs), 3) if n else None,
        "avg_win_r": round(gross_profit / len(wins), 3) if wins else None,
        "avg_loss_r": round(-gross_loss / len(losses), 3) if losses else None,
        # Average win ÷ average loss. With expectancy and profitable_rate this is
        # the whole picture: edge = rate × payoff − (1 − rate).
        "payoff_ratio": round(
            (gross_profit / len(wins)) / (gross_loss / len(losses)), 3
        ) if wins and losses else None,
        "best_r": round(max(rs), 3) if n else None,
        "worst_r": round(min(rs), 3) if n else None,
        "max_win_streak": streak_w,
        "max_loss_streak": streak_l,
        "max_r_drawdown": round(max_r_dd, 3),
    }


# --------------------------------------------------------------------------
# capital curve
# --------------------------------------------------------------------------

def capital_curve(trades, start_capital: float = 10_000.0, risk_pct: float = 1.0,
                  compounding: bool = True) -> dict:
    """Replay ``trades`` as capital and return the equity curve plus risk metrics.

    The walk is event-driven (every open, then every close, in time order) rather
    than a loop over closed trades, because sizing must happen at the moment risk
    was committed. Closing before opening at an identical timestamp is deliberate:
    capital freed by a close is available to the trade entered on that same bar,
    which is how a real account behaves.

    Returns ``points`` (one per close, so the curve is drawn from realized P&L
    only), the drawdown figures, and the exposure figures that say how much of
    ``risk_pct`` was actually at risk at once.
    """
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if start_capital <= 0:
        raise ValueError("start_capital must be positive")

    ordered = sorted(trades, key=lambda t: (t.opened, t.closed))
    # (time, phase, index). At one timestamp the order is: closes of positions opened
    # EARLIER (phase 0) -> opens (1) -> closes of positions opened at this same
    # instant (2). Phase 0 before 1 is deliberate: capital freed by a close is
    # available to a position entered on that same bar, as in a real account. Phase 2
    # exists because a zero-duration trade (resolved in the same bar it was issued, or
    # a stored row with no resolved_at) would otherwise settle before it was ever
    # opened — its P&L would vanish from the curve while still being counted in the
    # trade metrics, and the open-count would go negative.
    events = []
    for i, t in enumerate(ordered):
        events.append((t.opened, 1, i))
        events.append((t.closed, 0 if t.closed > t.opened else 2, i))
    events.sort()

    equity = start_capital
    peak = start_capital
    peak_at = ordered[0].opened if ordered else None
    risk_at: dict[int, float] = {}
    points: list[dict] = []
    open_n = 0
    max_concurrent = 0
    # Seconds during which at least one position was open, accumulated as the
    # open-count crosses zero. This is exposure time (PRD §6 item 18).
    exposed_since: int | None = None
    exposed_seconds = 0
    max_dd = 0.0
    dd_peak_at = dd_trough_at = None
    # Longest stretch spent below a previous equity high, in seconds. Measured
    # peak -> recovery (not peak -> trough): what a subscriber actually endures is
    # the wait to get back to even, and it is routinely several times longer than
    # the fall itself. ``underwater_since`` is the time of the peak currently being
    # recovered from, and is None whenever equity is at a high — so a curve that
    # never dips reports 0 days underwater rather than its whole span.
    longest_underwater = 0
    underwater_since: int | None = None
    ruined = False

    for ts, phase, i in events:
        if phase == 1:  # open — commit risk
            base = equity if compounding else start_capital
            risk_at[i] = max(base, 0.0) * risk_pct / 100.0
            open_n += 1
            max_concurrent = max(max_concurrent, open_n)
            if exposed_since is None:
                exposed_since = ts
            continue

        # close — realize P&L
        equity += risk_at.pop(i, 0.0) * ordered[i].r
        open_n -= 1
        if open_n == 0 and exposed_since is not None:
            exposed_seconds += ts - exposed_since
            exposed_since = None
        if equity <= 0:
            # Account wiped out. Keep walking so the trade count stays honest, but
            # equity floors at zero and every later trade sizes off nothing.
            equity = 0.0
            ruined = True

        if equity >= peak:
            if underwater_since is not None:
                longest_underwater = max(longest_underwater, ts - underwater_since)
                underwater_since = None
            peak, peak_at = equity, ts
        elif underwater_since is None:
            underwater_since = peak_at  # the high this drawdown is measured from
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd, dd_peak_at, dd_trough_at = dd, peak_at, ts
        points.append({
            "time": ts,
            "equity": round(equity, 2),
            "peak": round(peak, 2),
            "drawdown_pct": round(dd, 3),
        })

    if exposed_since is not None and ordered:  # still open at the end of the sample
        exposed_seconds += ordered[-1].closed - exposed_since
    if underwater_since is not None and points:
        # Still below a previous high when the sample ends: the drawdown has not
        # recovered, so it is measured to the last point rather than left out.
        longest_underwater = max(longest_underwater, points[-1]["time"] - underwater_since)

    first = ordered[0].opened if ordered else None
    last = points[-1]["time"] if points else None
    span = (last - first) if (first is not None and last is not None) else 0
    years = span / YEAR_SECONDS if span > 0 else 0.0
    total_return = (equity / start_capital - 1) * 100

    return {
        "start_capital": round(start_capital, 2),
        "end_capital": round(equity, 2),
        "total_return_pct": round(total_return, 2),
        # Compound annual growth rate. Only meaningful over a real span, and only
        # over a positive ending balance — a ruined account has no growth rate.
        "cagr_pct": (round(((equity / start_capital) ** (1 / years) - 1) * 100, 2)
                     if years >= 0.5 and equity > 0 else None),
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_from": dd_peak_at,
        "max_drawdown_at": dd_trough_at,
        "longest_underwater_days": round(longest_underwater / DAY_SECONDS, 1),
        "max_concurrent": max_concurrent,
        # Share of the sample's wall-clock span with at least one position open.
        "exposure_pct": round(exposed_seconds / span * 100, 1) if span > 0 else None,
        # What max_concurrent means in money: the most that was ever simultaneously
        # at risk, as a % of equity. The headline risk_pct is per TRADE, not total.
        "peak_risk_pct": round(max_concurrent * risk_pct, 2),
        "risk_pct": risk_pct,
        "compounding": compounding,
        "ruined": ruined,
        "first_trade": first,
        "last_trade": last,
        "years": round(years, 2),
        "points": points,
    }


# --------------------------------------------------------------------------
# calendar periods
# --------------------------------------------------------------------------

def period_returns(points, start_capital: float, period: str = "year") -> list[dict]:
    """Equity return per calendar year or month (UTC), oldest first.

    Periods with no closed trade are omitted rather than reported as 0% — a flat
    month and a month the strategy sat out are different facts, and the trade count
    on each row says which is which.
    """
    if period not in ("year", "month"):
        raise ValueError("period must be 'year' or 'month'")
    buckets: dict[str, dict] = {}
    prev_equity = start_capital
    for p in points:
        key = _period_key(p["time"], period)
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {"period": key, "start_equity": prev_equity,
                                "end_equity": p["equity"], "trades": 0,
                                "max_drawdown_pct": 0.0}
        b["end_equity"] = p["equity"]
        b["trades"] += 1
        b["max_drawdown_pct"] = max(b["max_drawdown_pct"], p["drawdown_pct"])
        prev_equity = p["equity"]

    rows = []
    for key in sorted(buckets):
        b = buckets[key]
        start = b["start_equity"]
        rows.append({
            **b,
            "start_equity": round(start, 2),
            "end_equity": round(b["end_equity"], 2),
            "return_pct": round((b["end_equity"] / start - 1) * 100, 2) if start > 0 else None,
            "max_drawdown_pct": round(b["max_drawdown_pct"], 2),
        })
    return rows


def risk_adjusted(monthly: list[dict]) -> dict:
    """Sharpe-like and Sortino-like figures from monthly equity returns.

    Sharpe-LIKE and Sortino-LIKE on purpose: the risk-free rate is taken as zero
    and the inputs are monthly strategy returns, not daily marks, so these are
    comparable BETWEEN runs of this engine and should not be quoted against a
    fund's published Sharpe. Below 12 months they are not reported at all —
    annualizing a handful of months produces a confident-looking number from
    almost no information.
    """
    rets = [m["return_pct"] for m in monthly if m["return_pct"] is not None]
    n = len(rets)
    if n < 12:
        return {"months": n, "sharpe": None, "sortino": None,
                "best_month_pct": round(max(rets), 2) if rets else None,
                "worst_month_pct": round(min(rets), 2) if rets else None,
                "positive_months": sum(1 for r in rets if r > 0),
                "note": "needs 12+ months of closed trades to annualize"}
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    downside = [r for r in rets if r < 0]
    dd = math.sqrt(sum(r * r for r in downside) / n) if downside else 0.0
    ann = math.sqrt(12)
    return {
        "months": n,
        "sharpe": round(mean / sd * ann, 2) if sd else None,
        "sortino": round(mean / dd * ann, 2) if dd else None,
        "best_month_pct": round(max(rets), 2),
        "worst_month_pct": round(min(rets), 2),
        "positive_months": sum(1 for r in rets if r > 0),
        "note": "zero risk-free rate, from monthly returns — compare only to other "
                "runs of this engine",
    }


# --------------------------------------------------------------------------
# benchmark
# --------------------------------------------------------------------------

def buy_and_hold(candles, start_capital: float = 10_000.0,
                 start_ts: int | None = None, end_ts: int | None = None) -> dict | None:
    """Buy-and-hold over the same window, for the PRD §16 comparison.

    Without this the strategy's return is unjudgeable: +120% over a cycle in which
    simply holding returned +400% is a worse outcome for the user, and a
    performance page that omits the comparison is hiding that. Drawdown is measured
    on closes, matching how the strategy curve is marked (realized points only).
    """
    rows = [c for c in candles
            if (start_ts is None or c["time"] >= start_ts)
            and (end_ts is None or c["time"] <= end_ts)]
    if len(rows) < 2:
        return None
    first, last = float(rows[0]["close"]), float(rows[-1]["close"])
    if first <= 0:
        return None
    peak = first
    max_dd = 0.0
    for c in rows:
        close = float(c["close"])
        peak = max(peak, close)
        max_dd = max(max_dd, (peak - close) / peak * 100)
    years = (rows[-1]["time"] - rows[0]["time"]) / YEAR_SECONDS
    growth = last / first
    return {
        "start_price": first,
        "end_price": last,
        "start_capital": round(start_capital, 2),
        "end_capital": round(start_capital * growth, 2),
        "total_return_pct": round((growth - 1) * 100, 2),
        "cagr_pct": (round((growth ** (1 / years) - 1) * 100, 2)
                     if years >= 0.5 and growth > 0 else None),
        "max_drawdown_pct": round(max_dd, 2),
        "years": round(years, 2),
    }


# --------------------------------------------------------------------------
# one-call summary
# --------------------------------------------------------------------------

def summarize(trades, start_capital: float = 10_000.0, risk_pct: float = 1.0,
              compounding: bool = True, include_points: bool = False) -> dict:
    """Everything the performance page needs for ONE segment of history.

    One segment: the caller runs this per backtest / out-of-sample / paper / live
    window and labels each result, because the PRD forbids blending them into a
    single figure. ``include_points`` keeps the full curve (for charting); the
    default drops it so a metrics-only caller isn't handed thousands of points.
    """
    curve = capital_curve(trades, start_capital, risk_pct, compounding)
    monthly = period_returns(curve["points"], start_capital, "month")
    yearly = period_returns(curve["points"], start_capital, "year")
    out = {
        "trade_metrics": trade_metrics(trades),
        "capital": {k: v for k, v in curve.items() if k != "points"},
        "monthly": monthly,
        "yearly": yearly,
        "risk_adjusted": risk_adjusted(monthly),
    }
    if include_points:
        out["points"] = curve["points"]
    return out


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def report_lines(trades, start_capital: float = 10_000.0, risk_pct: float = 1.0,
                 compounding: bool = True, benchmark=None, monthly: bool = False,
                 indent: str = "    ") -> tuple[list[str], dict]:
    """Render one segment's curve as plain text, and hand back the summary.

    The renderer lives here rather than in a management command because two
    commands print this block — ``backtest`` for historical and out-of-sample
    windows, ``feed_stats`` for the live delivered feed — and the PRD's whole claim
    is that those figures are computed and presented the same way. A second copy of
    the formatting is a second chance for the public number to drift from the
    researched one. Returns ``(lines, summary)`` so the caller can colour its own
    warnings off the metrics without recomputing them.
    """
    summary = summarize(trades, start_capital, risk_pct, compounding)
    cap, tm, ra = summary["capital"], summary["trade_metrics"], summary["risk_adjusted"]
    out: list[str] = []

    def row(k, v):
        out.append(f"{indent}{k:<18} {v}")

    if not trades:
        out.append(f"{indent}no closed trades in this segment")
        return out, summary

    row("sizing", f"{risk_pct:g}% of equity per trade, "
                  f"{'compounding' if compounding else 'fixed off starting capital'}")
    row("capital", f"${cap['start_capital']:,.0f} -> ${cap['end_capital']:,.2f}   "
                   f"({cap['total_return_pct']:+.2f}%)")
    row("span", f"{cap['years']:g} yr, {tm['trades']} positions"
                + (f", CAGR {cap['cagr_pct']:+.2f}%/yr" if cap["cagr_pct"] is not None
                   else ", too short for a CAGR"))
    row("max drawdown", f"-{cap['max_drawdown_pct']:.2f}%   "
                        f"(longest underwater {cap['longest_underwater_days']:g} days)")
    pf = f"{tm['profit_factor']:.2f}" if tm["profit_factor"] is not None else "n/a (no losses)"
    row("profit factor", f"{pf}   expectancy {tm['expectancy_r']:+.3f}R/trade   "
                         f"net {tm['net_r']:+.1f}R")
    payoff = f"{tm['payoff_ratio']:.2f}x" if tm["payoff_ratio"] is not None else "n/a"
    # Flats are named rather than folded in: "48% profitable" over a sample with 30
    # scratches is a different fact from the same figure with none, and the rate's
    # denominator is every position either way.
    row("profitable", f"{tm['profitable_rate']}% of positions made money net of costs"
                      f"   payoff {payoff}"
                      + (f"   ({tm['flat']} closed flat)" if tm["flat"] else ""))
    row("worst run", f"{tm['max_loss_streak']} losses in a row   "
                     f"max R drawdown {tm['max_r_drawdown']:.1f}R")
    # The headline risk_pct is PER TRADE. Concurrency multiplies it, and a curve that
    # quietly ran 8% exposed while labelled "1% risk" would mislead about the one
    # input drawdown depends on most.
    row("concurrency", f"up to {cap['max_concurrent']} positions at once = "
                       f"{cap['peak_risk_pct']:g}% of equity at risk; in market "
                       f"{cap['exposure_pct']}% of the span")
    if ra["sharpe"] is not None:
        row("monthly", f"Sharpe-like {ra['sharpe']}  Sortino-like {ra['sortino']}  "
                       f"best {ra['best_month_pct']:+.1f}%  "
                       f"worst {ra['worst_month_pct']:+.1f}%  "
                       f"({ra['positive_months']}/{ra['months']} months positive)")
    elif ra["months"]:
        row("monthly", f"{ra['positive_months']}/{ra['months']} months positive "
                       f"({ra['note']})")
    if cap["ruined"]:
        out.append(f"{indent}RUINED — equity reached zero; "
                   "every later trade sized off nothing.")

    for period, rows in (("year", summary["yearly"]),
                         ("month", summary["monthly"] if monthly else [])):
        if len(rows) < 2:
            continue
        out.append("")
        # Column widths match the row format below exactly — a performance table
        # whose headers sit over the wrong columns is how a drawdown gets read as a
        # return.
        out.append(f"{indent}{period:<11}   return   max DD  trades")
        for r in rows:
            out.append(f"{indent}{r['period']:<11} {r['return_pct']:+7.2f}%  "
                       f"{r['max_drawdown_pct']:6.2f}%  {r['trades']:>6}")

    if benchmark:
        bh = buy_and_hold(benchmark, start_capital, cap["first_trade"], cap["last_trade"])
        if bh:
            out.append("")
            out.append(
                f"{indent}BTC buy & hold over the same window: "
                f"{bh['total_return_pct']:+.2f}% "
                + (f"(CAGR {bh['cagr_pct']:+.2f}%/yr) " if bh["cagr_pct"] is not None else "")
                + f"with a -{bh['max_drawdown_pct']:.2f}% drawdown")
            beat = cap["total_return_pct"] > bh["total_return_pct"]
            shallower = cap["max_drawdown_pct"] < bh["max_drawdown_pct"]
            out.append(f"{indent}-> the strategy {'BEAT' if beat else 'LOST TO'} holding "
                       f"on return, with a {'shallower' if shallower else 'deeper'} "
                       "drawdown")
    return out, summary


# §35: the product has to be willing to reject its own strategies, which only works
# if the rejection criterion is stated as loudly as the return. Callers print this in
# their error style when profit_factor < 1.
REJECT_NOTE = ("Profit factor is below 1.0 — this segment LOSES money however it is "
               "sized. Not a shippable strategy (newPRD §13, §35).")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _median(values) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _period_key(ts: int, period: str) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return f"{dt.year:04d}" if period == "year" else f"{dt.year:04d}-{dt.month:02d}"
