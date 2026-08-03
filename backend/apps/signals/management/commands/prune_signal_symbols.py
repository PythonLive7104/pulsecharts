"""Find symbols whose DELIVERED signals lose money, and stop scanning them.

The strategies are not equally suited to all ~200 symbols. A 3-4.5xATR stop sits
outside routine noise on a major and inside a single wick on a thin low-float alt —
so the same setup that works on BTC bleeds elsewhere, while looking identical on the
card. Because the scan covers the union of every watchlist (and a Pro account is
seeded with every symbol), those coins are being scanned whether or not anyone wants
signals for them.

This ranks symbols by realized expectancy over DELIVERED, RESOLVED trades and
disables the scan for the ones that clear a minimum sample and lose. It never
touches is_active, so a disabled symbol stays chartable, searchable and
watchlistable — only the signal scan skips it.

    manage.py prune_signal_symbols                 # dry run, 90d, n>=15
    manage.py prune_signal_symbols --apply
    manage.py prune_signal_symbols --restore-all   # re-enable everything

WHY THE SAMPLE FLOOR MATTERS: with a few hundred trades spread over dozens of
symbols, a coin showing -1.5R over 4 trades is noise, and pruning it is superstition
with a spreadsheet. --min-trades exists to stop exactly that; don't lower it below
about 15 without a structural reason (e.g. you already know the book is thin).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.market_data.models import Symbol
from apps.signals.models import Signal, SignalDelivery

# Realized R per outcome under the live 50/25/25 scale-out (§19.2). Mirrors
# stats.SCALEOUT_R / feed_stats.SCALEOUT_R — change one, change all three.
SCALEOUT_R = {0: -1.0, 1: 0.5, 2: 1.0, 3: 1.75}


class Command(BaseCommand):
    help = "Disable signal scanning for symbols whose delivered trades lose money."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90,
                            help="Look back this many days of deliveries (default 90).")
        parser.add_argument("--min-trades", type=int, default=15,
                            help="Only judge symbols with at least this many resolved "
                                 "trades (default 15). Below ~15 the ranking is noise.")
        parser.add_argument("--max-r", type=float, default=0.0,
                            help="Disable symbols whose expectancy is below this, in R "
                                 "(default 0.0 — i.e. losing).")
        parser.add_argument("--apply", action="store_true",
                            help="Actually disable them. Without this it's a dry run.")
        parser.add_argument("--restore-all", action="store_true",
                            help="Re-enable scanning on every symbol and exit.")

    def handle(self, *args, **opts):
        w = self.stdout.write

        if opts["restore_all"]:
            n = Symbol.objects.filter(signals_enabled=False).update(signals_enabled=True)
            w(self.style.SUCCESS(f"Re-enabled signal scanning on {n} symbol(s)."))
            return

        since = timezone.now() - timedelta(days=opts["days"])
        signal_ids = set(
            SignalDelivery.objects.filter(delivered_at__gte=since)
            .values_list("signal_id", flat=True)
        )
        if not signal_ids:
            w(self.style.WARNING(f"No signals delivered in the last {opts['days']} days."))
            return

        # Collapse to the TRADE grain before scoring: several strategies calling the
        # same setup share (symbol, timeframe, direction, entry) and resolve together,
        # so counting rows would weight a coin by how many strategies liked it.
        trades: dict[tuple, Signal] = {}
        for s in (Signal.objects.filter(id__in=signal_ids)
                  .select_related("symbol").order_by("generated_at")):
            key = (s.symbol_id, s.timeframe, s.direction, round(s.entry_price, 8))
            prev = trades.get(key)
            if (prev is None
                    or (prev.outcome == Signal.Outcome.PENDING
                        and s.outcome != Signal.Outcome.PENDING)
                    or s.best_tp > prev.best_tp):
                trades[key] = s

        stats: dict[int, dict] = defaultdict(
            lambda: {"n": 0, "wins": 0, "r": 0.0, "ticker": "", "enabled": True,
                     "asset_class": ""}
        )
        for s in trades.values():
            if s.outcome == Signal.Outcome.PENDING:
                continue
            # Trend-flip invalidations close flat — a scratch, not evidence about the
            # symbol. Excluded from both the count and the R.
            if s.outcome == Signal.Outcome.INVALIDATED and s.best_tp == 0:
                continue
            b = stats[s.symbol_id]
            b["ticker"] = s.symbol.ticker
            b["enabled"] = s.symbol.signals_enabled
            b["asset_class"] = s.symbol.asset_class
            b["n"] += 1
            b["r"] += SCALEOUT_R.get(s.best_tp, 0.5) if s.best_tp >= 1 else -1.0
            if s.best_tp >= 1:
                b["wins"] += 1

        if not stats:
            w(self.style.WARNING("No resolved delivered trades in the window."))
            return

        rows = sorted(
            ({"id": sid, **b, "exp": b["r"] / b["n"]} for sid, b in stats.items()),
            key=lambda r: r["exp"],
        )
        judged = [r for r in rows if r["n"] >= opts["min_trades"]]
        losers = [r for r in judged if r["exp"] < opts["max_r"]]

        w(self.style.MIGRATE_HEADING(
            f"\nPer-symbol expectancy — delivered trades, last {opts['days']} days"))
        w(f"  symbols with trades : {len(rows)}")
        w(f"  judged (n >= {opts['min_trades']}) : {len(judged)}"
          f"   ·  too few trades to judge: {len(rows) - len(judged)}")
        w("")
        for r in rows:
            mark = "  " if r["n"] >= opts["min_trades"] else " ~"  # ~ = sample too small
            flag = "" if r["enabled"] else "   [already off]"
            win = r["wins"] / r["n"] * 100
            w(f"{mark}{r['ticker']:14s} n={r['n']:<4d} win={win:5.1f}%  "
              f"exp={r['exp']:+.2f}R{flag}")
        w(self.style.WARNING("  ~ = fewer than the minimum sample; NOT judged."))

        # Per-symbol rows are noise at this sample size; the asset-class aggregate
        # pools them into a number that can actually be read. This is the level at
        # which a decision is available today.
        groups: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0})
        for r in rows:
            g = groups[r["asset_class"] or "unknown"]
            g["n"] += r["n"]
            g["wins"] += r["wins"]
            g["r"] += r["r"]
        w(self.style.MIGRATE_HEADING("\n  Aggregate by asset class (pools the small samples)"))
        tot_n = tot_r = tot_w = 0
        for name, g in sorted(groups.items()):
            tot_n += g["n"]; tot_r += g["r"]; tot_w += g["wins"]
            w(f"  {name:12s} n={g['n']:<5d} win={g['wins'] / g['n'] * 100:5.1f}%  "
              f"exp={g['r'] / g['n']:+.2f}R")
        if tot_n:
            w(f"  {'ALL':12s} n={tot_n:<5d} win={tot_w / tot_n * 100:5.1f}%  "
              f"exp={tot_r / tot_n:+.2f}R")
        w(f"  spread across {len(rows)} symbols — {tot_n / len(rows):.1f} trades each on average.")

        if not losers:
            w(self.style.SUCCESS(
                f"\nNothing to prune: no symbol with n >= {opts['min_trades']} "
                f"is below {opts['max_r']:+.2f}R."))
            return

        w(self.style.MIGRATE_HEADING(f"\n{len(losers)} symbol(s) below the bar:"))
        for r in losers:
            w(f"  {r['ticker']:14s} n={r['n']:<4d} exp={r['exp']:+.2f}R")

        if not opts["apply"]:
            w(self.style.WARNING("\nDry run — nothing changed. Re-run with --apply."))
            return

        n = Symbol.objects.filter(id__in=[r["id"] for r in losers]).update(
            signals_enabled=False
        )
        w(self.style.SUCCESS(
            f"\nDisabled signal scanning on {n} symbol(s). They stay chartable and "
            "watchlistable; only the scan skips them. Undo with --restore-all."))
