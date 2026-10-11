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
    manage.py feed_stats --days 365 --monthly     # the capital curve, month by month

Win rate and expectancy are not the product claim; the capital curve is (newPRD §2,
§6). So the delivered feed is also replayed as MONEY — equity, drawdown, profit
factor, CAGR — through apps.signals.performance, the same engine and the same
renderer `backtest` uses for historical and out-of-sample windows. This block is the
LIVE segment of the track record and must never be pooled with the backtested one.

Counts each TRADE once, not each delivery: several users receiving the same signal is
one trade, and the same setup called by several strategies collapses to the trade
grain (symbol, timeframe, direction, entry) the way the feed itself collapses it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.signals import performance
from apps.signals.models import Signal, SignalDelivery

from apps.signals.stats import outcome_r, trade_r

# Realized R comes from stats.trade_r (win/loss tables) and stats.outcome_r (the
# capital curve, which also values flat closes), both scored under each trade's own
# Signal.exit_model — one definition shared with the accuracy stats.


def _blank(label):
    return {"label": label, "n": 0, "wins": 0, "r": 0.0, "tp": defaultdict(int)}


def _record(b, best_tp, won, exit_model="scaleout"):
    b["n"] += 1
    if won:
        b["wins"] += 1
        b["tp"][best_tp] += 1
    b["r"] += trade_r(exit_model, best_tp)


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
        parser.add_argument("--by-strategy-direction", action="store_true",
                            help="Cross-tab strategy x BUY/SELL. A directional regime "
                                 "(everything trending one way) hits fades and trend "
                                 "strategies differently, so a flat by-direction table "
                                 "can't say whether the weak side is one strategy selling "
                                 "into a rally or every strategy doing it — and those need "
                                 "opposite fixes.")
        parser.add_argument("--by-day", action="store_true",
                            help="Break down per UTC day, split BUY/SELL. Shows whether a "
                                 "weak side is a persistent bias or one bad session, and "
                                 "whether a config change actually moved anything after "
                                 "the date it went live.")
        parser.add_argument("--by-symbol", action="store_true",
                            help="Also break down per symbol — finds which coins carry the losses.")
        parser.add_argument("--capital", type=float, default=10000.0, metavar="USD",
                            help="Starting capital for the capital curve (default 10000).")
        parser.add_argument("--risk-pct", type=float, default=1.0, metavar="PCT",
                            help="Risk per trade as %% of equity for the curve (default "
                                 "1.0). Per TRADE: concurrent trades multiply it, and the "
                                 "curve reports the peak.")
        parser.add_argument("--no-compound", action="store_true",
                            help="Size off the STARTING capital rather than current "
                                 "equity, isolating edge from compounding.")
        parser.add_argument("--gross", action="store_true",
                            help="Do NOT charge trading costs. The curve is net of "
                                 "BACKTEST_COST_PCT by default (per asset class, charged "
                                 "as a fraction of each trade's own stored risk_pct), so "
                                 "a gross figure can't be mistaken for a realized one.")
        parser.add_argument("--monthly", action="store_true",
                            help="Add month-by-month returns to the capital curve.")
        parser.add_argument("--no-curve", action="store_true",
                            help="Skip the capital curve (R-space tables only).")
        parser.add_argument("--include-open", action="store_true",
                            help="Also list still-open trades (excluded from the stats).")

    def _trades(self, resolved, gross: bool):
        """Resolved delivered signals as chronological positions for the curve.

        Scored with ``stats.outcome_r`` under each row's own ``exit_model``, so a
        trade is always valued under the plan it was issued with — the same function
        the user-facing accuracy figure uses. Unlike the win-rate tables above, flat
        closes are KEPT: a trend-flip scratch is 0R, not a missing trade, and dropping
        it would understate both exposure and the trade count the curve rests on.

        Costs are charged per trade as BACKTEST_COST_PCT / this trade's stored
        risk_pct, so a wide-stop trade correctly pays proportionally less of its R.
        Without this the live curve would be gross while the backtest curve is net,
        and the two would not be comparable — which is the only reason to compute
        them the same way.
        """
        trades = []
        for s in resolved:
            r = outcome_r(s.exit_model, s.outcome)
            if not gross and s.risk_pct:
                r -= settings.BACKTEST_COST_PCT.get(s.symbol.asset_class, 0.0) / s.risk_pct
            opened = int(s.generated_at.timestamp())
            closed = int((s.resolved_at or s.generated_at).timestamp())
            trades.append(performance.Trade(
                opened=opened, closed=max(closed, opened), r=r,
                symbol=s.symbol.ticker, strategy=s.service.slug))
        return trades

    def _report_capital(self, resolved, opts):
        """The LIVE segment of the track record, in money (newPRD §15, §16)."""
        trades = self._trades(resolved, opts["gross"])
        if not trades:
            return
        lines, summary = performance.report_lines(
            trades, opts["capital"], opts["risk_pct"], not opts["no_compound"],
            monthly=bool(opts["monthly"]), indent="  ")
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n  Capital curve — LIVE delivered feed"
            + ("  (GROSS — no costs)" if opts["gross"] else "  (net of costs)")))
        for line in lines:
            self.stdout.write(line)
        pf = summary["trade_metrics"]["profit_factor"]
        if pf is not None and pf < 1.0:
            self.stdout.write(self.style.ERROR("  " + performance.REJECT_NOTE))
        self.stdout.write(self.style.WARNING(
            "  This is the LIVE segment only. Never pool it with a backtested or "
            "out-of-sample curve (newPRD §5, §39)."))

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
        by_strat_tf, by_strat_dir, by_day = {}, {}, {}
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
                _record(bucket, s.best_tp, won, s.exit_model)
            if opts["by_symbol"]:
                _record(by_symbol.setdefault(s.symbol.ticker, _blank(s.symbol.ticker)),
                        s.best_tp, won, s.exit_model)
            if opts["by_strategy_timeframe"]:
                key = (s.service.name, s.timeframe)
                _record(by_strat_tf.setdefault(key, _blank(f"{s.service.name} · {s.timeframe}")),
                        s.best_tp, won, s.exit_model)
            if opts["by_strategy_direction"]:
                key = (s.service.name, s.direction)
                _record(by_strat_dir.setdefault(key, _blank(f"{s.service.name} · {s.direction}")),
                        s.best_tp, won, s.exit_model)
            if opts["by_day"]:
                # Bucketed by GENERATION day, not delivery: a config change alters what
                # the scan produces, so generation time is what lines up with the date
                # it went live.
                day = s.generated_at.strftime("%Y-%m-%d")
                _record(by_day.setdefault((day, s.direction),
                                          _blank(f"{day} {s.direction}")), s.best_tp, won, s.exit_model)
            if opts["by_confluence"]:
                # Null = delivered before the count was stored. Bucketed separately
                # rather than assumed to be 1, which would fabricate the comparison.
                key = f"{s.confluence_count} agreed" if s.confluence_count else "unknown"
                _record(by_conf.setdefault(key, _blank(key)), s.best_tp, won, s.exit_model)

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
        if opts["by_strategy_direction"]:
            w(self.style.MIGRATE_HEADING("  By strategy x direction"))
            for name in sorted({k[0] for k in by_strat_dir}):
                rows = [by_strat_dir[k] for k in sorted(by_strat_dir) if k[0] == name]
                for b in rows:
                    w(_line(b))
                if len(rows) < 2:
                    w("      (one direction only — nothing to compare)")
        if opts["by_day"]:
            w(self.style.MIGRATE_HEADING("  By day x direction (UTC, generation date)"))
            for day in sorted({k[0] for k in by_day}):
                for b in [by_day[k] for k in sorted(by_day) if k[0] == day]:
                    w(_line(b))
        if opts["by_symbol"]:
            w(self.style.MIGRATE_HEADING("  By symbol (worst first)"))
            for b in sorted(by_symbol.values(), key=lambda x: x["r"] / max(x["n"], 1)):
                w(_line(b))

        if opts["include_open"] and pending:
            w(self.style.MIGRATE_HEADING("  Still open"))
            for s in pending:
                w(f"  {s.symbol.ticker:12s} {s.direction:4s} {s.timeframe:3s} "
                  f"best_tp={s.best_tp}  {s.generated_at:%Y-%m-%d %H:%M}")

        if not opts["no_curve"]:
            self._report_capital(resolved, opts)

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
