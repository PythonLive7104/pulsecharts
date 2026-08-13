"""Expectancy of the signals users were ACTUALLY SENT — not the raw candidate pool.

Every other measurement in this project (`backtest`) scores each strategy standalone:
every candidate it would ever emit, with no confidence floor and no confluence
requirement. The product is narrower than that. A signal only reaches a user when it
clears ``SIGNAL_MIN_CONFIDENCE`` *and* ``SIGNAL_CONFLUENCE_MIN`` strategies agree on
the same (symbol, timeframe, direction) — so the delivered feed is a small, filtered,
higher-conviction subset, and a backtest expectancy near zero is entirely compatible
with a delivered feed that is fine (or vice versa).

This command measures that subset from stored rows: no market data fetched, nothing
written. It is the honest answer to "are my signals any good", and the figure to
track through a live window.

    manage.py feed_stats --days 14
    manage.py feed_stats --days 30 --by-symbol

Counts each TRADE once, not each delivery: several users receiving the same signal is
one trade, and the same setup called by several strategies collapses to the trade
grain (symbol, timeframe, direction, entry) the way the feed itself collapses it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.signals.models import Signal, SignalDelivery

# Realized R per outcome under the live 50/25/25 scale-out with the stop moved to
# breakeven once TP1 tags (§19.2). Mirrors stats.SCALEOUT_R and backtest.SCALEOUT_R —
# change one, change all three.
SCALEOUT_R = {0: -1.0, 1: 0.5, 2: 1.0, 3: 1.75}


def _blank(label):
    return {"label": label, "n": 0, "wins": 0, "r": 0.0, "tp": defaultdict(int)}


def _record(b, best_tp, won):
    b["n"] += 1
    if won:
        b["wins"] += 1
        b["tp"][best_tp] += 1
        b["r"] += SCALEOUT_R.get(best_tp, 0.5)
    else:
        b["r"] -= 1.0


def _line(b):
    n = b["n"]
    if not n:
        return f"  {b['label']:26s} —  (no resolved trades)"
    win = b["wins"] / n * 100
    exp = b["r"] / n
    avg_win = (b["r"] + (n - b["wins"])) / b["wins"] if b["wins"] else 0.0
    tps = " ".join(f"TP{k}:{b['tp'][k]}" for k in (1, 2, 3) if b["tp"][k])
    return (f"  {b['label']:26s} {win:5.1f}%  {b['wins']:4d}W/{n - b['wins']:4d}L  n={n:<5d} "
            f"exp={exp:+.2f}R  avg win={avg_win:+.2f}R  {tps}")


class Command(BaseCommand):
    help = "Win rate + expectancy of DELIVERED signals (what users actually received)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14,
                            help="Look back this many days over delivery time (default 14).")
        parser.add_argument("--by-confluence", action="store_true",
                            help="Break down by how many strategies agreed — the evidence "
                                 "for where SIGNAL_CONFLUENCE_MIN should sit. Only covers "
                                 "signals delivered after the count started being stored.")
        parser.add_argument("--by-strategy-timeframe", action="store_true",
                            help="Cross-tab strategy x timeframe. Answers whether a weak "
                                 "timeframe is genuinely weak, or just where the weak "
                                 "STRATEGIES happen to concentrate — the two call for "
                                 "different fixes (drop the timeframe vs disable the "
                                 "strategies) and the flat by-timeframe table can't tell "
                                 "them apart.")
        parser.add_argument("--by-symbol", action="store_true",
                            help="Also break down per symbol — finds which coins carry the losses.")
        parser.add_argument("--include-open", action="store_true",
                            help="Also list still-open trades (excluded from the stats).")

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts["days"])

        # Distinct signals that were delivered to at least one user in the window.
        # Delivery rows are per user; the signal is the trade.
        signal_ids = set(
            SignalDelivery.objects.filter(delivered_at__gte=since)
            .values_list("signal_id", flat=True)
        )
        if not signal_ids:
            self.stdout.write(self.style.WARNING(
                f"No signals delivered in the last {opts['days']} days."))
            return

        signals = list(
            Signal.objects.filter(id__in=signal_ids)
            .select_related("symbol", "service")
            .order_by("generated_at")
        )

        # Collapse to the TRADE grain: strategies that called the same setup share
        # (symbol, timeframe, direction, entry), resolve together, and must not be
        # counted as several independent results.
        trades = {}
        for s in signals:
            key = (s.symbol_id, s.timeframe, s.direction, round(s.entry_price, 8))
            prev = trades.get(key)
            # Keep the furthest-progressed row for the trade; a resolved row is truth.
            if (prev is None
                    or (prev.outcome == Signal.Outcome.PENDING and s.outcome != Signal.Outcome.PENDING)
                    or s.best_tp > prev.best_tp):
                trades[key] = s

        resolved = [s for s in trades.values() if s.outcome != Signal.Outcome.PENDING]
        pending = [s for s in trades.values() if s.outcome == Signal.Outcome.PENDING]

        overall = _blank("ALL DELIVERED")
        by_strategy, by_tf, by_symbol, by_dir, by_conf = {}, {}, {}, {}, {}
        by_strat_tf = {}
        invalidated = 0

        for s in resolved:
            # A trend-flip invalidation is a 0R scratch, not a loss — exclude it from
            # win/loss so it can't flatter or damage the ratio.
            if s.outcome == Signal.Outcome.INVALIDATED and s.best_tp == 0:
                invalidated += 1
                continue
            won = s.best_tp >= 1
            for bucket, label in (
                (overall, None),
                (by_strategy.setdefault(s.service.name, _blank(s.service.name)), None),
                (by_tf.setdefault(s.timeframe, _blank(s.timeframe)), None),
                (by_dir.setdefault(s.direction, _blank(s.direction)), None),
            ):
                _record(bucket, s.best_tp, won)
            if opts["by_symbol"]:
                _record(by_symbol.setdefault(s.symbol.ticker, _blank(s.symbol.ticker)),
                        s.best_tp, won)
            if opts["by_strategy_timeframe"]:
                key = (s.service.name, s.timeframe)
                _record(by_strat_tf.setdefault(key, _blank(f"{s.service.name} · {s.timeframe}")),
                        s.best_tp, won)
            if opts["by_confluence"]:
                # Null = delivered before the count was stored. Bucketed separately
                # rather than assumed to be 1, which would fabricate the comparison.
                key = f"{s.confluence_count} agreed" if s.confluence_count else "unknown"
                _record(by_conf.setdefault(key, _blank(key)), s.best_tp, won)

        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(
            f"\nDelivered-feed results — last {opts['days']} days"))
        w(f"  delivered signal rows : {len(signals)}")
        w(f"  distinct trades       : {len(trades)}")
        w(f"  resolved              : {len(resolved)}   (open: {len(pending)})")
        if invalidated:
            w(f"  trend-flip scratches  : {invalidated}  (0R, excluded from win/loss)")
        w("")
        w(_line(overall))
        w("")
        w(self.style.MIGRATE_HEADING("  By strategy"))
        for b in sorted(by_strategy.values(), key=lambda x: -x["n"]):
            w(_line(b))
        w(self.style.MIGRATE_HEADING("  By timeframe"))
        for b in sorted(by_tf.values(), key=lambda x: x["label"]):
            w(_line(b))
        w(self.style.MIGRATE_HEADING("  By direction"))
        for b in sorted(by_dir.values(), key=lambda x: x["label"]):
            w(_line(b))
        if opts["by_confluence"]:
            w(self.style.MIGRATE_HEADING("  By strategies agreeing"))
            for b in sorted(by_conf.values(), key=lambda x: x["label"]):
                w(_line(b))
        if opts["by_strategy_timeframe"]:
            w(self.style.MIGRATE_HEADING("  By strategy x timeframe"))
            # Grouped by strategy, timeframes adjacent, so the 1h-vs-4h comparison for
            # ONE strategy is on consecutive lines — that pairing is the whole point.
            for name in sorted({k[0] for k in by_strat_tf}):
                rows = [by_strat_tf[k] for k in sorted(by_strat_tf) if k[0] == name]
                for b in rows:
                    w(_line(b))
                if len(rows) < 2:
                    w("      (one timeframe only — nothing to compare)")
        if opts["by_symbol"]:
            w(self.style.MIGRATE_HEADING("  By symbol (worst first)"))
            for b in sorted(by_symbol.values(), key=lambda x: x["r"] / max(x["n"], 1)):
                w(_line(b))

        if opts["include_open"] and pending:
            w(self.style.MIGRATE_HEADING("  Still open"))
            for s in pending:
                w(f"  {s.symbol.ticker:12s} {s.direction:4s} {s.timeframe:3s} "
                  f"best_tp={s.best_tp}  {s.generated_at:%Y-%m-%d %H:%M}")

        n = overall["n"]
        if n:
            need = (n - overall["wins"]) / overall["wins"] if overall["wins"] else 0
            w("")
            w("  Break-even needs the average winner above "
              f"{need:+.2f}R at this win rate.")
        w(self.style.WARNING(
            "\n  Delivered feed only — the population users actually received "
            "(confidence floor + confluence). Not comparable to `backtest`, which\n"
            "  scores every strategy standalone. Small sample; not an accuracy claim (§13.7)."))
