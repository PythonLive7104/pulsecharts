"""Backtest the strategies on historical Hyperliquid candles (§13.7).

Two modes:

  Rule-based (default, free, no LLM):
      python manage.py backtest
      python manage.py backtest --timeframes 1h,4h --max-symbols 20 --candles 500
    Replays past candles through each strategy's pre-gate + directional bias and
    walks the *subsequent* price to see whether each signal hit a take-profit or
    its stop. Reports realized win rate and expectancy per strategy.

  LLM comparison (paid — uses your OpenAI key):
      python manage.py backtest --llm
      python manage.py backtest --llm --llm-sample 100
    On a capped sample of the *same* setups, also asks the real LLM (the live
    engine's decision-maker) for its call, then compares LLM-gated expectancy to
    the rule-based expectancy head-to-head. This is the only way to know whether
    the LLM's selectivity actually improves results.

No database writes. Caveats are printed in the output footer — read them.
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.market_data.feeds import get_candles
from apps.market_data.models import Symbol
from apps.signals.engine import generate_judgment
from apps.signals.evaluate import walk
from apps.signals.indicators import compute_indicators, _ema, _market_structure
from apps.signals.levels import TP_MULTIPLES, compute_levels
from apps.signals.models import SignalService
from apps.signals import pregate
from apps.signals.pregate import (
    EMA_STACK_EXEMPT,
    candidate_direction,
    confidence_score,
    passes_pregate,
)
from apps.signals.tasks import _HTF_MAP

MIN_CANDLES = 210  # enough history for the 200 EMA / swing windows (matches tasks.py)


# Realized R per best-TP under the live 50/25/25 scale-out model (§19.2): bank ½ at
# TP1, ¼ at TP2, ¼ at TP3, stop to breakeven after TP1 so any unfilled tranche closes
# flat. TP1 = ½×1R, TP2 = ½×1R + ¼×2R, TP3 = ½×1R + ¼×2R + ¼×3R. (See EXIT_MODELS
# below for the head-to-head that chose this over even thirds.)
SCALEOUT_R = {1: 0.5, 2: 1.0, 3: 1.75, 4: 3.0}


# Candidate trade-management schemes to compare head-to-head, each as the fraction of
# the position banked at (TP1, TP2, TP3). All assume the house rule "stop → breakeven
# after TP1", so any un-banked remainder that doesn't reach its target closes at 0R
# (never a post-TP1 loss). Expectancy is therefore exactly derivable from the winners-
# by-best-TP distribution — no per-trade replay needed. Used only for reporting; the
# live/stored geometry is unchanged.
EXIT_MODELS = [
    ("all off @TP1               ", (1.0, 0.0, 0.0)),
    ("even thirds  (old model)   ", (1 / 3, 1 / 3, 1 / 3)),
    ("½ TP1 · ¼ TP2 · ¼ TP3 (live)", (0.5, 0.25, 0.25)),
    ("½ TP1 · ½ TP2              ", (0.5, 0.5, 0.0)),
    ("⅔ TP1 · ⅓ TP2              ", (2 / 3, 1 / 3, 0.0)),
]


def _exit_expectancy(total, fractions):
    """Per-trade R for a management scheme (fractions banked at TP1/TP2/TP3), computed
    from the aggregate winners-by-best-TP distribution + losses. Breakeven-after-TP1
    means an un-banked tranche contributes its TP multiple only if that TP was reached,
    else 0; every loss is a full -1R."""
    n = total["trades"]
    if not n:
        return 0.0
    f1, f2, f3 = fractions
    d = total["tp_dist"]
    r = 0.0
    r += d[1] * (f1 * 1)                        # topped at TP1: only the TP1 tranche pays
    r += d[2] * (f1 * 1 + f2 * 2)               # reached TP2: TP1 + TP2 tranches
    r += d[3] * (f1 * 1 + f2 * 2 + f3 * 3)      # reached TP3: all three
    r -= total["losses"] * 1.0
    return r / n


def _blank(name):
    return {
        "name": name, "trades": 0, "wins": 0, "losses": 0,
        "r_tp1": 0.0, "r_scale": 0.0, "r_best": 0.0, "mfe": 0.0, "mae": 0.0,
        "tp_dist": {1: 0, 2: 0, 3: 0, 4: 0},
    }


def _record(bucket, res):
    bucket["trades"] += 1
    best_tp = res["best_tp"]
    if best_tp >= 1:
        bucket["wins"] += 1
        bucket["tp_dist"][best_tp] += 1
        bucket["r_tp1"] += 1.0                       # conservative: exit all at TP1 (+1R)
        bucket["r_scale"] += SCALEOUT_R[best_tp]     # realized: 50/25/25 scale-out
        bucket["r_best"] += TP_MULTIPLES[best_tp]    # optimistic: exit all at best target
    else:
        bucket["losses"] += 1
        bucket["r_tp1"] -= 1.0
        bucket["r_scale"] -= 1.0
        bucket["r_best"] -= 1.0
    bucket["mfe"] += res["mfe_pct"]
    bucket["mae"] += res["mae_pct"]


# --- exit lab -------------------------------------------------------------
# Management schemes that CANNOT be derived from the winners-by-best-TP aggregate
# (EXIT_MODELS above), because they depend on the price path after TP1: a later
# breakeven point, no breakeven at all, or a trailing stop. Each is
#   (label, banked fractions at TP1/TP2/TP3, breakeven after TP-n or None, ATR trail
#    multiple applied to the runner once TP1 banks or None).
# All are replayed in ONE pass over the same candles, so adding schemes is cheap and
# every scheme is scored on exactly the same trades.
EXIT_LAB = [
    ("live 50/25/25, BE@TP1     ", (0.5, 0.25, 0.25), 1, None),
    ("50/25/25, BE@TP2          ", (0.5, 0.25, 0.25), 2, None),
    ("50/25/25, no BE           ", (0.5, 0.25, 0.25), None, None),
    ("all off @TP1              ", (1.0, 0.0, 0.0), 1, None),
    ("50% TP1 + 50% trail 2xATR ", (0.5, 0.0, 0.0), 1, 2.0),
    ("50% TP1 + 50% trail 3xATR ", (0.5, 0.0, 0.0), 1, 3.0),
    ("33/33/34, BE@TP1          ", (1 / 3, 1 / 3, 1 / 3), 1, None),
]

# Bars of forward path replayed per trade. The base walk stops at its own resolution;
# schemes that hold a runner can outlive it, so they need their own horizon — capped
# so a never-resolving trade can't walk the whole series.
REPLAY_BARS = 300


def _replay_exits(direction, entry, stop0, tps, future, atr):
    """Realized R per scheme in EXIT_LAB, from one pass over the forward candles.

    Intrabar ambiguity is resolved the same way evaluate.walk does: when a bar touches
    both a stop and a target, whichever is nearer the open is assumed first, and with
    no open available the stop wins (worst case). Returns None if the trade is
    degenerate (no risk distance).
    """
    buy = direction == "BUY"
    risk = abs(entry - stop0)
    if not risk:
        return None

    def to_r(price):
        return ((price - entry) if buy else (entry - price)) / risk

    # Per-scheme state: banked R, un-banked fraction, its current stop, TPs banked.
    state = [{"r": 0.0, "left": 1.0, "stop": stop0, "hit": 0} for _ in EXIT_LAB]
    peak = entry  # running extreme, for the trailing schemes

    for c in future[:REPLAY_BARS]:
        hi, lo, opn = c["high"], c["low"], c.get("open")
        peak = max(peak, hi) if buy else min(peak, lo)
        bar_tp = 0
        for i, tp in enumerate(tps, start=1):
            if (hi >= tp) if buy else (lo <= tp):
                bar_tp = i

        for st, (_, banks, be_at, trail) in zip(state, EXIT_LAB):
            if st["left"] <= 0:
                continue
            # Trailing stop rides the running extreme once TP1 has banked.
            if trail and st["hit"] >= 1 and atr:
                trailed = (peak - trail * atr) if buy else (peak + trail * atr)
                st["stop"] = max(st["stop"], trailed) if buy else min(st["stop"], trailed)
            sl_hit = (lo <= st["stop"]) if buy else (hi >= st["stop"])
            new_tp = max(st["hit"], bar_tp)

            if sl_hit and new_tp > st["hit"]:
                stop_first = (abs(opn - st["stop"]) <= abs(opn - tps[0])) if opn is not None else True
                if stop_first:
                    st["r"] += st["left"] * to_r(st["stop"])
                    st["left"] = 0.0
                    continue
            elif sl_hit:
                st["r"] += st["left"] * to_r(st["stop"])
                st["left"] = 0.0
                continue

            # Bank every target newly reached this bar.
            for level in range(st["hit"] + 1, new_tp + 1):
                frac = banks[level - 1] if level - 1 < len(banks) else 0.0
                frac = min(frac, st["left"])
                if frac:
                    st["r"] += frac * to_r(tps[level - 1])
                    st["left"] -= frac
            st["hit"] = new_tp
            if be_at is not None and st["hit"] >= be_at:
                st["stop"] = max(st["stop"], entry) if buy else min(st["stop"], entry)
            if st["left"] <= 1e-9 or st["hit"] >= len(tps) and not trail:
                # Fixed-ladder schemes close the last tranche at the final target.
                if st["left"] > 1e-9 and st["hit"] >= len(tps):
                    st["r"] += st["left"] * to_r(tps[-1])
                    st["left"] = 0.0

    # Anything still open at the horizon closes at its scheme's stop — conservative,
    # and identical across schemes so the comparison stays fair.
    for st in state:
        if st["left"] > 1e-9:
            st["r"] += st["left"] * to_r(st["stop"])
            st["left"] = 0.0
    return [st["r"] for st in state]


def _outcome(direction, snap, future, asset_class="crypto", strategy_slug=None,
             atr_floor=None, atr_cap=None):
    """Deterministic levels + walk for a setup; None if degenerate or unresolved.

    Mean-reversion setups get their own (much tighter) ATR stop band — with the trend
    band their TP1 would sit further away than the trade was ever going, so they'd
    look worthless for a reason that has nothing to do with the strategy.
    """
    if strategy_slug and pregate.kind_of(strategy_slug) == pregate.KIND_REVERSION:
        floor = settings.SIGNAL_ATR_FLOOR_REVERSION
        cap = settings.SIGNAL_ATR_CAP_REVERSION
    else:
        # --atr-floor/--atr-cap sweep the TREND band only; reversion keeps its own
        # (a fade with a trend-width stop can't reach TP1 by construction).
        floor = atr_floor if atr_floor is not None else (
            settings.SIGNAL_ATR_STOP_FLOOR.get(asset_class) or settings.SIGNAL_ATR_STOP_FLOOR["crypto"])
        cap = atr_cap if atr_cap is not None else (
            settings.SIGNAL_ATR_STOP_CAP.get(asset_class) or settings.SIGNAL_ATR_STOP_CAP["crypto"])
    levels = compute_levels(
        direction, float(snap["close"]), float(snap["atr"]),
        float(snap["swing_high"]), float(snap["swing_low"]),
        atr_stop_mult=floor, max_atr_mult=cap,
    )
    if levels is None:
        return None
    tps = [levels[k] for k in ("tp1", "tp2", "tp3", "tp4") if levels[k] is not None]
    res = walk(direction, float(snap["close"]), levels["stop_loss"], tps, future)
    if not (res["terminal"] or res["best_tp"] >= 1):
        return None
    res["_levels"] = (levels["stop_loss"], tps)  # for --exit-lab's replay
    return res


def _totals(stats):
    t = _blank("ALL")
    for b in stats.values():
        for k in ("trades", "wins", "losses", "r_tp1", "r_scale", "r_best", "mfe", "mae"):
            t[k] += b[k]
        for n in (1, 2, 3, 4):
            t["tp_dist"][n] += b["tp_dist"][n]
    return t


def _tp_breakdown(b):
    """'TP1:5 TP2:4 TP3:2 TP4:1' — how far winners ran (each counted at its best TP)."""
    d = b["tp_dist"]
    return " ".join(f"TP{n}:{d[n]}" for n in (1, 2, 3, 4))


class Command(BaseCommand):
    help = "Backtest strategies on historical candles; optionally compare LLM vs rule-based."

    def add_arguments(self, parser):
        parser.add_argument("--timeframes", default=None,
                            help="Comma list, e.g. 1h,4h (default: SIGNAL_TIMEFRAMES).")
        parser.add_argument("--asset-class", default=None, choices=["crypto", "forex"],
                            help="Restrict to one asset class. WITHOUT this the symbol "
                                 "list is whatever order the DB returns, which in practice "
                                 "is all crypto — so every parameter tuned on a plain run "
                                 "is crypto-derived even though it also governs forex.")
        parser.add_argument("--max-symbols", type=int, default=20,
                            help="How many active symbols to test (default 20).")
        parser.add_argument("--candles", type=int, default=500,
                            help="Candles of history per symbol/timeframe (default 500).")
        parser.add_argument("--llm", action="store_true",
                            help="Also run the real LLM on a sample and compare (uses OpenAI).")
        parser.add_argument("--llm-sample", type=int, default=80,
                            help="Max LLM calls when --llm is set (cost cap; default 80).")
        parser.add_argument("--include-inactive", action="store_true",
                            help="Also backtest is_active=False strategies (validate before activating).")
        parser.add_argument("--ema-gate", default=None,
                            choices=["stack200", "stack50", "filter200"],
                            help="Override the EMA-alignment gate for this run (compare trend strictness).")
        parser.add_argument("--fib", action="store_true",
                            help="Enable the Fib-pullback gate for this run (default off).")
        parser.add_argument("--fib-min", type=float, default=0.5,
                            help="Fib-pullback zone floor when --fib is set (default 0.5).")
        parser.add_argument("--fib-max", type=float, default=0.786,
                            help="Fib-pullback zone cap when --fib is set (default 0.786).")
        parser.add_argument("--rsi-overbought", type=float, default=None,
                            help="Override RSI overbought cap for this run (0 disables the bound).")
        parser.add_argument("--rsi-oversold", type=float, default=None,
                            help="Override RSI oversold cap for this run (0 disables the bound).")
        parser.add_argument("--no-ema200", action="store_true",
                            help="Drop the 200-EMA trend filter from every strategy trigger "
                                 "(direction rests on the fast EMAs; pair with --fib).")
        parser.add_argument("--structure", action="store_true",
                            help="Enable the market-structure trend filter (non-breakout "
                                 "signals need HH+HL for BUY / LH+LL for SELL). Additive to "
                                 "the EMA gates; combine with --no-ema200 to test structure "
                                 "standing in for the 200 EMA.")
        parser.add_argument("--no-structure", action="store_true",
                            help="Force the market-structure filter OFF. Needed because the "
                                 "env (SIGNAL_STRUCTURE_TREND_FILTER) is applied at startup, "
                                 "so --structure alone can only ever turn it ON — with the env "
                                 "already True, both spellings of the run were identical.")
        parser.add_argument("--htf-structure", action="store_true",
                            help="Require the next-higher timeframe's swing structure (per "
                                 "_HTF_MAP: 1h→4h, 4h→1d) to agree with the signal. "
                                 "Point-in-time aligned; breakout strategies exempt.")
        parser.add_argument("--htf-bias", action="store_true",
                            help="Require the next-higher timeframe's 200-EMA bias (per "
                                 "_HTF_MAP: 1h\u21924h, 4h\u21921d) to agree with the signal — "
                                 "price above the higher frame's 200 EMA allows BUYs only, "
                                 "below allows SELLs only. This is the live HTF regime gate "
                                 "(SIGNAL_HTF_REGIME_ENABLED), which the plain backtest skips. "
                                 "Point-in-time aligned; no strategy is exempt, matching live.")
        parser.add_argument("--exit-lab", action="store_true",
                            help="Also replay alternative trade-management schemes per trade "
                                 "(later breakeven, no breakeven, ATR trails) and report each "
                                 "one's expectancy on the SAME trades. Costs one extra pass "
                                 "over the forward candles per trade.")
        parser.add_argument("--reversion-adx-max", type=float, default=None,
                            help="ADX CEILING for mean-reversion strategies (live: "
                                 "SIGNAL_ADX_MAX_REVERSION, default 20). Fades only fire at or "
                                 "below it. Raising it toward the trend floor (SIGNAL_ADX_MIN) "
                                 "closes the band where neither family can fire.")
        parser.add_argument("--reversion-htf", action="store_true",
                            help="Apply the higher-timeframe 200-EMA bias to MEAN-REVERSION "
                                 "strategies only (the live SIGNAL_REVERSION_HTF_GUARD): a "
                                 "fade must agree with the higher frame's trend, so it buys "
                                 "dips in an uptrend rather than catching a falling knife. "
                                 "Unlike --htf-bias this leaves trend strategies untouched.")
        parser.add_argument("--min-confidence", type=int, default=None,
                            help="Apply the delivery CONFIDENCE floor (pregate.confidence_score), "
                                 "which the plain backtest otherwise skips entirely — so a run "
                                 "without this models a feed with no conviction gate at all. "
                                 "Live value is SIGNAL_MIN_CONFIDENCE.")
        parser.add_argument("--min-confidence-strategy", action="append", default=None,
                            metavar="SLUG:FLOOR",
                            help="Per-strategy confidence floor override, repeatable "
                                 "(e.g. --min-confidence-strategy rsi2-reversion:75). Live: "
                                 "SIGNAL_MIN_CONFIDENCE_BY_STRATEGY. Beats both the kind and "
                                 "global floors. The three fades respond to the score in "
                                 "different directions, so no single number suits them all.")
        parser.add_argument("--min-confidence-reversion", type=int, default=None,
                            help="Separate confidence floor for MEAN-REVERSION strategies "
                                 "(live: SIGNAL_MIN_CONFIDENCE_REVERSION). Only meaningful "
                                 "alongside --min-confidence, which sets the trend/breakout "
                                 "floor. Fades score on a different confidence branch, so the "
                                 "flat floor cuts them at the wrong place.")
        parser.add_argument("--atr-floor", type=float, default=None,
                            help="Override the TREND stop's minimum ATR multiple (live: 3.0 "
                                 "crypto / 2.0 forex). Tighter = smaller R, so every TP sits "
                                 "closer in %% terms — more stop-outs, but a reachable ladder.")
        parser.add_argument("--atr-cap", type=float, default=None,
                            help="Override the TREND stop's maximum ATR multiple (live: 4.5 "
                                 "crypto / 3.0 forex). Mean-reversion strategies keep their own "
                                 "tighter band either way.")
        parser.add_argument("--overext", type=float, default=None,
                            help="Override the overextension guard (ATR stretch beyond EMA21 "
                                 "that blocks a chase entry). 0 disables; live default is 2.0. "
                                 "Sweep to tune, e.g. --overext 1.5.")
        parser.add_argument("--adx-min", type=float, default=None,
                            help="Apply an ADX floor (proxy for the live regime filter's ADX "
                                 "gate, which the backtest otherwise SKIPS). Only setups with "
                                 "ADX >= this are traded — sweep to compare, e.g. 30 vs 25. "
                                 "NOTE: ADX-only; live also has the EMA-separation chop filter.")

    def handle(self, *args, **opts):
        from apps.signals import pregate
        if opts.get("ema_gate"):
            pregate.EMA_GATE_MODE = opts["ema_gate"]
            self.stdout.write(self.style.WARNING(f"EMA gate override: {opts['ema_gate']}"))
        if opts.get("fib"):
            pregate.FIB_PULLBACK_MIN = opts["fib_min"]
            pregate.FIB_PULLBACK_MAX = opts["fib_max"]
            self.stdout.write(self.style.WARNING(
                f"Fib-pullback gate ON: zone [{opts['fib_min']}, {opts['fib_max']}]"))
        if opts.get("rsi_overbought") is not None:
            pregate.RSI_OVERBOUGHT = opts["rsi_overbought"]
            self.stdout.write(self.style.WARNING(f"RSI overbought override: {opts['rsi_overbought']}"))
        if opts.get("rsi_oversold") is not None:
            pregate.RSI_OVERSOLD = opts["rsi_oversold"]
            self.stdout.write(self.style.WARNING(f"RSI oversold override: {opts['rsi_oversold']}"))
        if opts.get("no_ema200"):
            pregate.EMA200_TREND_FILTER = False
            self.stdout.write(self.style.WARNING(
                "200-EMA trend filter OFF (direction from fast EMAs; Fib zone confirms)."))
        if opts.get("structure"):
            pregate.STRUCTURE_TREND_FILTER = True
        if opts.get("no_structure"):
            pregate.STRUCTURE_TREND_FILTER = False
        if opts.get("overext") is not None:
            pregate.OVEREXT_ATR_MULT = opts["overext"]
            self.stdout.write(self.style.WARNING(
                f"Overextension guard override: {opts['overext']}×ATR beyond EMA21 "
                f"({'disabled' if opts['overext'] <= 0 else 'blocks chases'})."))
        htf_structure_on = bool(opts.get("htf_structure"))
        if htf_structure_on:
            self.stdout.write(self.style.WARNING(
                "HTF structure confluence ON (higher timeframe must agree; breakouts exempt)."))
        if opts.get("min_confidence") is not None:
            self.stdout.write(self.style.WARNING(
                f"Confidence floor ON: only setups scoring >= {opts['min_confidence']}."))
        # Parse --min-confidence-strategy SLUG:FLOOR (repeatable) the same way settings
        # parses SIGNAL_MIN_CONFIDENCE_BY_STRATEGY: fail loudly, never skip silently, so
        # a typo can't quietly leave a strategy on the kind floor.
        strategy_floors = {}
        for item in opts.get("min_confidence_strategy") or []:
            slug, sep, value = item.partition(":")
            if not sep or not value.strip().lstrip("-").isdigit():
                raise CommandError(
                    f"--min-confidence-strategy expects SLUG:FLOOR, got {item!r}"
                )
            strategy_floors[slug.strip()] = int(value)
        if strategy_floors and opts.get("min_confidence") is None:
            raise CommandError(
                "--min-confidence-strategy only applies when --min-confidence is set "
                "(without it the backtest applies no conviction gate at all)."
            )
        if opts.get("atr_floor") is not None or opts.get("atr_cap") is not None:
            self.stdout.write(self.style.WARNING(
                f"TREND stop band override: {opts.get('atr_floor')}-{opts.get('atr_cap')} xATR "
                "(reversion unchanged)."))
        htf_bias_on = bool(opts.get("htf_bias"))
        if htf_bias_on:
            self.stdout.write(self.style.WARNING(
                "HTF 200-EMA bias ON (higher timeframe's 200 EMA must agree; no exemptions)."))
        reversion_htf_on = bool(opts.get("reversion_htf"))
        if reversion_htf_on:
            self.stdout.write(self.style.WARNING(
                "Reversion HTF guard ON (fades must agree with the higher-timeframe trend)."))
        timeframes = (
            [t.strip() for t in opts["timeframes"].split(",") if t.strip()]
            if opts["timeframes"] else list(settings.SIGNAL_TIMEFRAMES)
        )

        # EFFECTIVE config, not just the overrides. Gates are module-level state that
        # SignalsConfig.ready() seeds from env BEFORE any flag is parsed, so a run
        # could silently inherit live settings — two runs meant to differ came out
        # byte-identical because the flag couldn't move an env-set gate. Printing the
        # resolved values makes every result self-documenting.
        self.stdout.write(self.style.MIGRATE_HEADING("Effective config:"))
        for label, value in [
            ("ema gate", pregate.EMA_GATE_MODE),
            ("200 EMA (own frame)", "ON" if pregate.EMA200_TREND_FILTER else "OFF"),
            ("market structure", "ON" if pregate.STRUCTURE_TREND_FILTER else "OFF"),
            ("HTF structure", "ON" if opts.get("htf_structure") else "OFF"),
            ("HTF 200-EMA bias", "ON" if opts.get("htf_bias") else "OFF"),
            ("fib pullback", f"[{pregate.FIB_PULLBACK_MIN}, {pregate.FIB_PULLBACK_MAX}]"
                             if pregate.FIB_PULLBACK_MIN else "OFF"),
            ("overext guard", f"{pregate.OVEREXT_ATR_MULT}xATR"
                              if pregate.OVEREXT_ATR_MULT else "OFF"),
            ("reversion ADX ceiling", opts.get("reversion_adx_max")
                                      if opts.get("reversion_adx_max") is not None
                                      else settings.SIGNAL_ADX_MAX_REVERSION),
            ("confidence floor", opts.get("min_confidence")
                                 if opts.get("min_confidence") is not None
                                 else "OFF (backtest skips the live gate)"),
            ("  ...for reversion", opts.get("min_confidence_reversion")
                                   if opts.get("min_confidence_reversion") is not None
                                   else "same as above"),
            ("  ...per strategy", ", ".join(opts["min_confidence_strategy"])
                                  if opts.get("min_confidence_strategy") else "none"),
            ("ADX floor", opts.get("adx_min") if opts.get("adx_min") is not None
                          else "OFF (backtest skips the live regime filter)"),
            ("asset class", opts.get("asset_class") or "all (DB order — in practice crypto)"),
            ("timeframes", ",".join(timeframes)),
            ("candles / symbols", f"{opts['candles']} / {opts['max_symbols']}"),
        ]:
            self.stdout.write(f"  {label:22s} {value}")
        svc_qs = SignalService.objects.all() if opts["include_inactive"] \
            else SignalService.objects.filter(is_active=True)
        services = list(svc_qs)
        if not services:
            self.stderr.write(self.style.ERROR("No signal services — run seed_signal_services."))
            return
        sym_qs = Symbol.objects.filter(is_active=True)
        if opts.get("asset_class"):
            sym_qs = sym_qs.filter(asset_class=opts["asset_class"])
        symbols = list(sym_qs[:opts["max_symbols"]])
        if not symbols:
            self.stderr.write(self.style.ERROR("No active symbols — run sync_symbols."))
            return

        llm_on = opts["llm"]
        if llm_on and not settings.OPENAI_API_KEY:
            self.stderr.write(self.style.ERROR("--llm needs OPENAI_API_KEY set."))
            return

        rb = {svc.slug: _blank(svc.name) for svc in services}
        # Per-scheme totals for --exit-lab: index-aligned with EXIT_LAB.
        exit_lab = {"on": bool(opts.get("exit_lab")), "n": 0, "r": [0.0] * len(EXIT_LAB)}
        llm = {svc.slug: _blank(svc.name) for svc in services} if llm_on else None
        # Shared LLM budget + call stats across all series.
        budget = {"left": opts["llm_sample"] if llm_on else 0,
                  "calls": 0, "taken": 0, "neutral": 0, "disagree": 0, "in": 0, "out": 0}

        mode = "LLM vs rule-based" if llm_on else "rule-based"
        self.stdout.write(
            f"Backtesting ({mode}): {len(services)} strategies × {len(symbols)} symbols × "
            f"{len(timeframes)} timeframes"
            + (f", up to {budget['left']} LLM calls" if llm_on else "")
            + "…"
        )

        series = 0
        for sym in symbols:
            for tf in timeframes:
                if llm_on and budget["left"] <= 0:
                    break
                try:
                    candles = get_candles(sym, tf, limit=opts["candles"])
                except (requests.RequestException, ValueError):
                    continue
                if len(candles) < MIN_CANDLES + 5:
                    continue
                self._run_series(sym, tf, candles, services, rb, llm, budget,
                                 sym.asset_class, htf_structure_on, opts["candles"],
                                 opts.get("adx_min"), htf_bias_on, exit_lab,
                                 reversion_htf_on, opts.get("min_confidence"),
                                 opts.get("atr_floor"), opts.get("atr_cap"),
                                 opts.get("reversion_adx_max"),
                                 opts.get("min_confidence_reversion"),
                                 strategy_floors)
                series += 1
                self.stdout.write(f"  · {sym.ticker} {tf}", ending="\r")
            if llm_on and budget["left"] <= 0:
                break

        self.stdout.write("")
        if llm_on:
            self._report_compare(rb, llm, budget)
        else:
            self._report(rb, series)
        if exit_lab["on"] and exit_lab["n"]:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n  Exit lab — replayed per trade (n={exit_lab['n']}), same trades:"))
            ranked = sorted(zip(EXIT_LAB, exit_lab["r"]), key=lambda x: -x[1])
            for (label, banks, be_at, trail), total in ranked:
                self.stdout.write(f"    {label} exp={total / exit_lab['n']:+.3f}R")
            self.stdout.write(
                "    (path-replayed, so trailing/later-BE schemes are exact rather than\n"
                "     derived from the winners-by-best-TP aggregate above.)")

    def _htf_timeline(self, sym, tf, htf_limit):
        """Sorted [(usable_from_time, structure), …] for the timeframe above `tf`.

        Entry j carries the swing structure computed from higher-timeframe bars 0..j
        (bar j closed), tagged with bar j+1's open time — the moment that structure
        becomes usable without lookahead. Returns None when there's no higher frame,
        the fetch fails, or there aren't enough bars to form structure.
        """
        htf = _HTF_MAP.get(tf)
        if not htf:
            return None
        try:
            hc = get_candles(sym, htf, limit=max(htf_limit, 300))
        except (requests.RequestException, ValueError):
            return None
        if len(hc) < 20:  # need a couple of pivots each side to classify anything
            return None
        return [(hc[j + 1]["time"], _market_structure(hc[: j + 1])[0])
                for j in range(len(hc) - 1)]

    def _htf_bias_timeline(self, sym, tf, htf_limit):
        """Sorted [(usable_from_time, 'up'|'down'), …] of the higher timeframe's
        200-EMA bias — the live HTF regime gate (tasks._htf_direction), which reads
        price vs the higher frame's 200 EMA: above = bullish, below = bearish.

        Entry j is computed from higher-timeframe bar j (closed) and tagged with bar
        j+1's open time, so it can never be used before it was knowable. Returns None
        when there's no higher frame, the fetch fails, or there isn't enough history
        for a 200 EMA — all of which fail OPEN, matching live's "ERR → allow".
        """
        htf = _HTF_MAP.get(tf)
        if not htf:
            return None
        try:
            hc = get_candles(sym, htf, limit=max(htf_limit, 300))
        except (requests.RequestException, ValueError):
            return None
        if len(hc) < 210:  # 200 EMA + a bar to hand it forward
            return None
        closes = [c["close"] for c in hc]
        ema200 = _ema(closes, 200)
        out = []
        for j in range(len(hc) - 1):
            e = ema200[j]
            if e is None:
                continue
            out.append((hc[j + 1]["time"], "up" if closes[j] > e else "down"))
        return out or None

    def _run_series(self, sym, tf, candles, services, rb, llm, budget,
                    asset_class="crypto", htf_structure_on=False, htf_limit=500,
                    adx_min=None, htf_bias_on=False, exit_lab=None,
                    reversion_htf_on=False, min_confidence=None,
                    atr_floor=None, atr_cap=None, rev_adx_max=None,
                    min_confidence_reversion=None, strategy_floors=None):
        ticker = sym.ticker
        n = len(candles)
        threshold = settings.SIGNAL_MIN_CONFIDENCE
        free_at = {svc.slug: MIN_CANDLES for svc in services}

        # Point-in-time HTF structure timeline: for each higher-timeframe bar, the
        # swing structure known *once it has closed*, tagged with the time it becomes
        # usable (the next HTF bar's open). At each signal bar we advance a pointer to
        # the latest entry usable by then — no lookahead. None entry = choppy HTF
        # (blocks); before the first entry = no data yet (fails open).
        htf_timeline = self._htf_timeline(sym, tf, htf_limit) if htf_structure_on else None
        hp = -1  # pointer into htf_timeline; -1 = nothing usable yet
        # Same point-in-time treatment for the HTF 200-EMA bias (--htf-bias).
        bias_timeline = (self._htf_bias_timeline(sym, tf, htf_limit)
                         if (htf_bias_on or reversion_htf_on) else None)
        bp = -1

        for i in range(MIN_CANDLES, n - 1):
            if llm is not None and budget["left"] <= 0:
                return
            snap = compute_indicators(candles[: i + 1])
            if not snap.get("atr") or not snap.get("close"):
                continue
            if snap.get("swing_high") is None or snap.get("swing_low") is None:
                continue
            # ADX floor — proxy for the live regime filter's ADX gate (which the
            # backtest otherwise skips). ADX is symbol/timeframe-level, so gate the
            # whole bar, matching how _regime_ok reads one ADX per (symbol, tf).
            # NOTE: applied per-strategy below, not here — the floor is a trend test
            # and mean reversion needs the opposite bound.
            bar_adx = snap.get("adx")
            future = candles[i + 1:]

            htf_bias_now = None  # 'up' | 'down' | None (no data yet → fail open)
            if bias_timeline is not None:
                t = candles[i]["time"]
                while bp + 1 < len(bias_timeline) and bias_timeline[bp + 1][0] <= t:
                    bp += 1
                htf_bias_now = bias_timeline[bp][1] if bp >= 0 else None

            htf_struct_now = None  # 'up' | 'down' | None (choppy) | 'SKIP' (no data)
            if htf_timeline is not None:
                t = candles[i]["time"]
                while hp + 1 < len(htf_timeline) and htf_timeline[hp + 1][0] <= t:
                    hp += 1
                htf_struct_now = htf_timeline[hp][1] if hp >= 0 else "SKIP"

            for svc in services:
                if i < free_at[svc.slug] or not passes_pregate(svc.slug, snap):
                    continue
                direction = candidate_direction(svc.slug, snap)
                if direction not in ("BUY", "SELL"):
                    continue

                # Regime bound, by strategy kind: trend/breakout need ADX at or above
                # the floor; a fade needs it at or below the reversion ceiling (it is
                # only sane when no strong trend is running).
                if adx_min is not None:
                    if pregate.kind_of(svc.slug) == pregate.KIND_REVERSION:
                        ceiling = (rev_adx_max if rev_adx_max is not None
                                   else settings.SIGNAL_ADX_MAX_REVERSION)
                        if bar_adx is None or bar_adx > ceiling:
                            continue
                    elif bar_adx is None or bar_adx < adx_min:
                        continue

                # HTF 200-EMA bias. --htf-bias applies it to every strategy;
                # --reversion-htf applies it to fades only, mirroring the live
                # SIGNAL_REVERSION_HTF_GUARD. None = not knowable yet → allow, as
                # live fails open.
                applies = htf_bias_on or (
                    reversion_htf_on
                    and pregate.kind_of(svc.slug) == pregate.KIND_REVERSION
                )
                if applies and htf_bias_now is not None:
                    if htf_bias_now != ("up" if direction == "BUY" else "down"):
                        continue

                # HTF structure confluence (breakouts exempt, mirroring the live gate).
                if (htf_timeline is not None and htf_struct_now != "SKIP"
                        and svc.slug not in EMA_STACK_EXEMPT):
                    want = "up" if direction == "BUY" else "down"
                    if htf_struct_now != want:  # opposite trend or choppy (None) → skip
                        continue

                # Conviction floor — the same score the live feed gates on. Mean
                # reversion may carry its own floor (SIGNAL_MIN_CONFIDENCE_REVERSION):
                # fades score on a different branch of confidence_score, so one flat
                # number cuts the two families at different effective strictnesses.
                if min_confidence is not None:
                    floor = min_confidence
                    if (min_confidence_reversion is not None
                            and pregate.kind_of(svc.slug) == pregate.KIND_REVERSION):
                        floor = min_confidence_reversion
                    # Per-strategy override beats both, matching confluence.min_confidence.
                    if strategy_floors and svc.slug in strategy_floors:
                        floor = strategy_floors[svc.slug]
                    if confidence_score(direction, snap, svc.slug) < floor:
                        continue

                res = _outcome(direction, snap, future, asset_class, svc.slug,
                               atr_floor, atr_cap)
                if res is None:
                    free_at[svc.slug] = i + 1
                    continue
                free_at[svc.slug] = i + 1 + res["bars"]

                if exit_lab and exit_lab["on"]:
                    stop0, tps = res["_levels"]
                    rs = _replay_exits(direction, float(snap["close"]), stop0, tps,
                                       future, snap.get("atr"))
                    if rs:
                        exit_lab["n"] += 1
                        for k, r in enumerate(rs):
                            exit_lab["r"][k] += r

                # Rule-based-only mode: record every candidate, move on.
                if llm is None:
                    _record(rb[svc.slug], res)
                    continue

                # LLM comparison: only spend the budget on paired candidates so
                # rule-based and LLM are scored on the *same* setups.
                if budget["left"] <= 0:
                    return
                try:
                    judgment, usage = generate_judgment(
                        ticker, tf, svc.name, svc.strategy_focus, snap
                    )
                except Exception:
                    continue
                budget["left"] -= 1
                budget["calls"] += 1
                if usage is not None:
                    budget["in"] += getattr(usage, "prompt_tokens", 0) or 0
                    budget["out"] += getattr(usage, "completion_tokens", 0) or 0

                _record(rb[svc.slug], res)  # paired rule-based outcome

                ldir = judgment.get("direction", "NEUTRAL")
                conf = int(judgment.get("confidence_pct", 0))
                if ldir not in ("BUY", "SELL") or conf < threshold:
                    budget["neutral"] += 1  # LLM declined this setup
                    continue
                budget["taken"] += 1
                if ldir != direction:
                    budget["disagree"] += 1
                lres = _outcome(ldir, snap, future, asset_class)
                if lres is not None:
                    _record(llm[svc.slug], lres)

    # --- reporting ---------------------------------------------------------

    def _line(self, b):
        t = b["trades"]
        if not t:
            return f"  {b['name']:<26} {'—':>7}  (no trades)"
        return (
            f"  {b['name']:<26} {b['wins']/t*100:5.1f}%  "
            f"{b['wins']:>3}W /{b['losses']:>3}L  n={t:<4} "
            f"exp(TP1)={b['r_tp1']/t:+.2f}R  exp(scale)={b['r_scale']/t:+.2f}R  "
            f"exp(best)={b['r_best']/t:+.2f}R  "
            f"avgMFE={b['mfe']/t:+.1f}% avgMAE={b['mae']/t:+.1f}%"
        )

    def _report(self, stats, series):
        rows = list(stats.values())
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nBacktest results  ({series} symbol×timeframe series)\n"
        ))
        rows.sort(key=lambda b: (b["trades"] == 0, -(b["wins"] / b["trades"] if b["trades"] else 0)))
        for b in rows:
            self.stdout.write(self._line(b))
        total = _totals(stats)
        self.stdout.write(self.style.SUCCESS("\n" + self._line(total).strip()))
        self.stdout.write(f"  winners by best TP: {_tp_breakdown(total)}")
        self._exit_model_report(total)
        self.stdout.write(self.style.WARNING(self._footer()))

    def _exit_model_report(self, total):
        """Compare trade-management schemes on the SAME resolved trades — how much of
        the per-trade edge each exit rule actually banks. Reframes the exp(TP1) vs
        exp(scale) gap as a management choice, not a fixed cost."""
        if not total["trades"]:
            return
        self.stdout.write(self.style.MIGRATE_HEADING("\n  Exit-model comparison (same trades):"))
        ranked = sorted(EXIT_MODELS, key=lambda m: -_exit_expectancy(total, m[1]))
        for label, fr in ranked:
            self.stdout.write(f"    {label}  exp={_exit_expectancy(total, fr):+.3f}R")

    def _report_compare(self, rb, llm, budget):
        rb_t, llm_t = _totals(rb), _totals(llm)

        def stat(b):
            n = b["trades"]
            if not n:
                return "    no trades"
            return (f"{n:>4} trades   win {b['wins']/n*100:5.1f}%   "
                    f"exp(TP1)={b['r_tp1']/n:+.2f}R   exp(best)={b['r_best']/n:+.2f}R")

        cost = (budget["in"] / 1e6 * settings.OPENAI_PRICE_IN_PER_1M
                + budget["out"] / 1e6 * settings.OPENAI_PRICE_OUT_PER_1M)

        self.stdout.write(self.style.MIGRATE_HEADING("\nLLM vs rule-based — same setups\n"))
        self.stdout.write(f"  Rule-based : {stat(rb_t)}")
        self.stdout.write(f"             winners by best TP: {_tp_breakdown(rb_t)}")
        self.stdout.write(f"  LLM-gated  : {stat(llm_t)}")
        self.stdout.write(f"             winners by best TP: {_tp_breakdown(llm_t)}")
        self.stdout.write(
            f"\n  LLM made {budget['calls']} calls on {budget['calls']} setups: "
            f"took {budget['taken']}, declined {budget['neutral']} "
            f"(NEUTRAL / below {settings.SIGNAL_MIN_CONFIDENCE}% confidence), "
            f"disagreed on direction {budget['disagree']}×.\n"
            f"  Tokens: {budget['in']} in / {budget['out']} out · est. cost ${cost:.4f}"
        )
        # How to read the comparison.
        if rb_t["trades"] and llm_t["trades"]:
            rb_e, llm_e = rb_t["r_tp1"] / rb_t["trades"], llm_t["r_tp1"] / llm_t["trades"]
            verdict = (
                "LLM filtering IMPROVED per-trade expectancy"
                if llm_e > rb_e else
                "LLM filtering did NOT improve per-trade expectancy"
            )
            self.stdout.write(self.style.SUCCESS(
                f"\n  → On this sample: {verdict} "
                f"({llm_e:+.2f}R vs {rb_e:+.2f}R per trade), "
                f"on {llm_t['trades']} LLM trades vs {rb_t['trades']} rule-based."
            ))
        self.stdout.write(self.style.WARNING(self._footer(llm=True)))

    def _footer(self, llm=False):
        base = (
            "\nReading this honestly:\n"
            "  • Win % = reached TP1 before the stop. exp(TP1) = exit all at TP1 (caps\n"
            "    winners at +1R, conservative). exp(scale) = the LIVE model: 50/25/25\n"
            "    scale-out (½ TP1, ¼ TP2, ¼ TP3), stop to breakeven after TP1 (what to\n"
            "    actually expect). exp(best) = exit all at the furthest TP (hindsight).\n"
            "  • Small historical sample, currently-listed coins only (survivorship),\n"
            "    one market regime. Directional, not proof — don't claim accuracy (§13.7)."
        )
        if llm:
            base += (
                "\n  • The LLM trades fewer setups (it declines some), so its sample is\n"
                "    smaller and noisier. Run a larger --llm-sample for a firmer read."
            )
        return base
