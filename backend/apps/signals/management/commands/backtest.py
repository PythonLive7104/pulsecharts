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
from django.core.management.base import BaseCommand

from apps.market_data.feeds import get_candles
from apps.market_data.models import Symbol
from apps.signals.engine import generate_judgment
from apps.signals.evaluate import walk
from apps.signals.indicators import compute_indicators, _market_structure
from apps.signals.levels import TP_MULTIPLES, compute_levels
from apps.signals.models import SignalService
from apps.signals.pregate import EMA_STACK_EXEMPT, candidate_direction, passes_pregate
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


def _outcome(direction, snap, future, asset_class="crypto"):
    """Deterministic levels + walk for a setup; None if degenerate or unresolved."""
    floor = settings.SIGNAL_ATR_STOP_FLOOR.get(asset_class) or settings.SIGNAL_ATR_STOP_FLOOR["crypto"]
    cap = settings.SIGNAL_ATR_STOP_CAP.get(asset_class) or settings.SIGNAL_ATR_STOP_CAP["crypto"]
    levels = compute_levels(
        direction, float(snap["close"]), float(snap["atr"]),
        float(snap["swing_high"]), float(snap["swing_low"]),
        atr_stop_mult=floor, max_atr_mult=cap,
    )
    if levels is None:
        return None
    res = walk(
        direction, float(snap["close"]), levels["stop_loss"],
        [levels[k] for k in ("tp1", "tp2", "tp3", "tp4") if levels[k] is not None], future,
    )
    return res if (res["terminal"] or res["best_tp"] >= 1) else None


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
        parser.add_argument("--htf-structure", action="store_true",
                            help="Require the next-higher timeframe's swing structure (per "
                                 "_HTF_MAP: 1h→4h, 4h→1d) to agree with the signal. "
                                 "Point-in-time aligned; breakout strategies exempt.")
        parser.add_argument("--overext", type=float, default=None,
                            help="Override the overextension guard (ATR stretch beyond EMA21 "
                                 "that blocks a chase entry). 0 disables; live default is 2.0. "
                                 "Sweep to tune, e.g. --overext 1.5.")

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
            self.stdout.write(self.style.WARNING(
                "Market-structure filter ON (BUY needs HH+HL, SELL needs LH+LL)."))
        if opts.get("overext") is not None:
            pregate.OVEREXT_ATR_MULT = opts["overext"]
            self.stdout.write(self.style.WARNING(
                f"Overextension guard override: {opts['overext']}×ATR beyond EMA21 "
                f"({'disabled' if opts['overext'] <= 0 else 'blocks chases'})."))
        htf_structure_on = bool(opts.get("htf_structure"))
        if htf_structure_on:
            self.stdout.write(self.style.WARNING(
                "HTF structure confluence ON (higher timeframe must agree; breakouts exempt)."))
        timeframes = (
            [t.strip() for t in opts["timeframes"].split(",") if t.strip()]
            if opts["timeframes"] else list(settings.SIGNAL_TIMEFRAMES)
        )
        svc_qs = SignalService.objects.all() if opts["include_inactive"] \
            else SignalService.objects.filter(is_active=True)
        services = list(svc_qs)
        if not services:
            self.stderr.write(self.style.ERROR("No signal services — run seed_signal_services."))
            return
        symbols = list(Symbol.objects.filter(is_active=True)[:opts["max_symbols"]])
        if not symbols:
            self.stderr.write(self.style.ERROR("No active symbols — run sync_symbols."))
            return

        llm_on = opts["llm"]
        if llm_on and not settings.OPENAI_API_KEY:
            self.stderr.write(self.style.ERROR("--llm needs OPENAI_API_KEY set."))
            return

        rb = {svc.slug: _blank(svc.name) for svc in services}
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
                                 sym.asset_class, htf_structure_on, opts["candles"])
                series += 1
                self.stdout.write(f"  · {sym.ticker} {tf}", ending="\r")
            if llm_on and budget["left"] <= 0:
                break

        self.stdout.write("")
        if llm_on:
            self._report_compare(rb, llm, budget)
        else:
            self._report(rb, series)

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

    def _run_series(self, sym, tf, candles, services, rb, llm, budget,
                    asset_class="crypto", htf_structure_on=False, htf_limit=500):
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

        for i in range(MIN_CANDLES, n - 1):
            if llm is not None and budget["left"] <= 0:
                return
            snap = compute_indicators(candles[: i + 1])
            if not snap.get("atr") or not snap.get("close"):
                continue
            if snap.get("swing_high") is None or snap.get("swing_low") is None:
                continue
            future = candles[i + 1:]

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

                # HTF structure confluence (breakouts exempt, mirroring the live gate).
                if (htf_timeline is not None and htf_struct_now != "SKIP"
                        and svc.slug not in EMA_STACK_EXEMPT):
                    want = "up" if direction == "BUY" else "down"
                    if htf_struct_now != want:  # opposite trend or choppy (None) → skip
                        continue

                res = _outcome(direction, snap, future, asset_class)
                if res is None:
                    free_at[svc.slug] = i + 1
                    continue
                free_at[svc.slug] = i + 1 + res["bars"]

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
