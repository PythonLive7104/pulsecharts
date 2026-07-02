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
from apps.signals.indicators import compute_indicators
from apps.signals.levels import TP_MULTIPLES, compute_levels
from apps.signals.models import SignalService
from apps.signals.pregate import candidate_direction, passes_pregate

MIN_CANDLES = 210  # enough history for the 200 EMA / swing windows (matches tasks.py)


def _blank(name):
    return {
        "name": name, "trades": 0, "wins": 0, "losses": 0,
        "r_tp1": 0.0, "r_best": 0.0, "mfe": 0.0, "mae": 0.0,
        "tp_dist": {1: 0, 2: 0, 3: 0, 4: 0},
    }


def _record(bucket, res):
    bucket["trades"] += 1
    best_tp = res["best_tp"]
    if best_tp >= 1:
        bucket["wins"] += 1
        bucket["tp_dist"][best_tp] += 1
        bucket["r_tp1"] += 1.0                       # conservative: exit at TP1 (+1R)
        bucket["r_best"] += TP_MULTIPLES[best_tp]    # optimistic: exit at best target
    else:
        bucket["losses"] += 1
        bucket["r_tp1"] -= 1.0
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
        for k in ("trades", "wins", "losses", "r_tp1", "r_best", "mfe", "mae"):
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
                self._run_series(sym.ticker, tf, candles, services, rb, llm, budget, sym.asset_class)
                series += 1
                self.stdout.write(f"  · {sym.ticker} {tf}", ending="\r")
            if llm_on and budget["left"] <= 0:
                break

        self.stdout.write("")
        if llm_on:
            self._report_compare(rb, llm, budget)
        else:
            self._report(rb, series)

    def _run_series(self, ticker, tf, candles, services, rb, llm, budget, asset_class="crypto"):
        n = len(candles)
        threshold = settings.SIGNAL_MIN_CONFIDENCE
        free_at = {svc.slug: MIN_CANDLES for svc in services}

        for i in range(MIN_CANDLES, n - 1):
            if llm is not None and budget["left"] <= 0:
                return
            snap = compute_indicators(candles[: i + 1])
            if not snap.get("atr") or not snap.get("close"):
                continue
            if snap.get("swing_high") is None or snap.get("swing_low") is None:
                continue
            future = candles[i + 1:]

            for svc in services:
                if i < free_at[svc.slug] or not passes_pregate(svc.slug, snap):
                    continue
                direction = candidate_direction(svc.slug, snap)
                if direction not in ("BUY", "SELL"):
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
            f"exp(TP1)={b['r_tp1']/t:+.2f}R  exp(best)={b['r_best']/t:+.2f}R  "
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
        self.stdout.write(self.style.WARNING(self._footer()))

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
            "  • Win % = reached TP1 before the stop. exp(TP1) = exit at TP1 (caps\n"
            "    winners at +1R, conservative). exp(best) = exit at the furthest\n"
            "    target hit (hindsight peak, optimistic). Reality is in between.\n"
            "  • Small historical sample, currently-listed coins only (survivorship),\n"
            "    one market regime. Directional, not proof — don't claim accuracy (§13.7)."
        )
        if llm:
            base += (
                "\n  • The LLM trades fewer setups (it declines some), so its sample is\n"
                "    smaller and noisier. Run a larger --llm-sample for a firmer read."
            )
        return base
