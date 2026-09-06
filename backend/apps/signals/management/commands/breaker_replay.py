"""Replay SIGNAL_LOSS_BREAKER against real delivered history.

The breaker is the first guard aimed at the failure mode that has actually cost
money (2026-08-19, 2026-09-03: loss clusters, not bad individual setups). Before
trusting a setting that can SILENCE the product, replay it over what really
happened and see what it would have suppressed.

Strictly point-in-time: a signal is judged only against stop-outs that had already
RESOLVED when it was delivered. Counting the whole day's losses would let the
breaker "know" about losses that had not happened yet and would flatter it enormously.

    manage.py breaker_replay --days 14
    manage.py breaker_replay --days 14 --config 6/4/6
    manage.py breaker_replay --days 14 --sweep

Reads only; writes nothing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.signals.models import Signal, SignalDelivery

# Mirrors feed_stats.SCALEOUT_R / stats.SCALEOUT_R — realized R per best-TP reached
# under the live 50/25/25 ladder with breakeven after TP1.
SCALEOUT_R = {0: -1.0, 1: 0.5, 2: 1.0, 3: 1.75}


def _parse(raw: str):
    parts = (raw or "").split("/")
    if len(parts) != 3:
        raise CommandError("--config expects losses/window_h/cooldown_h, e.g. 6/4/6")
    try:
        return int(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        raise CommandError("--config values must be numeric, e.g. 6/4/6") from None


class Command(BaseCommand):
    help = "Replay the loss circuit breaker over real delivered signals."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--config", default="6/4/6",
                            help="losses/window_h/cooldown_h (default 6/4/6).")
        parser.add_argument("--asset-class", default="crypto",
                            choices=["crypto", "forex"])
        parser.add_argument("--sweep", action="store_true",
                            help="Try a grid of settings instead of one. A setting that "
                                 "suppresses nothing is inert; one that suppresses most "
                                 "of the book is a kill switch, not a guard.")

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts["days"])
        ids = set(
            SignalDelivery.objects.filter(delivered_at__gte=since)
            .values_list("signal_id", flat=True)
        )
        if not ids:
            self.stdout.write(self.style.WARNING("No deliveries in window."))
            return

        rows = list(
            Signal.objects.filter(id__in=ids, symbol__asset_class=opts["asset_class"])
            .exclude(outcome=Signal.Outcome.PENDING)
            .select_related("symbol")
            .order_by("generated_at")
            .values("generated_at", "resolved_at", "outcome", "best_tp")
        )
        # A trend-flip invalidation closes flat at 0R. Excluded from win/loss here for
        # the same reason feed_stats excludes it: it is neither a hit nor a stop.
        rows = [r for r in rows
                if not (r["outcome"] == Signal.Outcome.INVALID and r["best_tp"] == 0)]
        if not rows:
            self.stdout.write(self.style.WARNING("No resolved trades in window."))
            return

        losses = sorted(r["resolved_at"] for r in rows
                        if r["outcome"] == Signal.Outcome.SL and r["resolved_at"])

        configs = ([opts["config"]] if not opts["sweep"] else
                   ["4/2/4", "4/4/6", "5/3/6", "6/4/6", "6/6/8", "8/4/6", "8/6/12"])

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nBreaker replay — {opts['asset_class']}, last {opts['days']}d, "
            f"{len(rows)} resolved trades"))
        base_w = sum(1 for r in rows if r["best_tp"] >= 1)
        base_l = len(rows) - base_w
        base_r = sum(SCALEOUT_R.get(r["best_tp"], 0.5) if r["best_tp"] >= 1 else -1.0
                     for r in rows)
        self.stdout.write(
            f"  {'NO BREAKER':14s} {base_w / len(rows) * 100:5.1f}%  "
            f"{base_w:3d}W/{base_l:3d}L  n={len(rows):<4d} exp={base_r / len(rows):+.2f}R")

        for cfg in configs:
            need, win_h, cool_h = _parse(cfg)
            kept, supp_w, supp_l = [], 0, 0
            for r in rows:
                t = r["generated_at"]
                # Only losses ALREADY RESOLVED at delivery time are knowable.
                prior = [x for x in losses if x <= t]
                halted = False
                if prior:
                    recent = [x for x in prior if x >= t - timedelta(hours=win_h)]
                    if len(recent) >= need:
                        halted = True
                    else:
                        cutoff = t - timedelta(hours=cool_h)
                        for i in range(len(prior) - 1, -1, -1):
                            anchor = prior[i]
                            if anchor < cutoff:
                                break
                            burst = [x for x in prior[:i + 1]
                                     if x >= anchor - timedelta(hours=win_h)]
                            if len(burst) >= need:
                                halted = True
                                break
                if halted:
                    if r["best_tp"] >= 1:
                        supp_w += 1
                    else:
                        supp_l += 1
                else:
                    kept.append(r)

            if not kept:
                self.stdout.write(self.style.WARNING(
                    f"  {cfg:14s} suppressed EVERYTHING — kill switch, not a guard."))
                continue
            w = sum(1 for r in kept if r["best_tp"] >= 1)
            l = len(kept) - w
            rr = sum(SCALEOUT_R.get(r["best_tp"], 0.5) if r["best_tp"] >= 1 else -1.0
                     for r in kept)
            self.stdout.write(
                f"  {cfg:14s} {w / len(kept) * 100:5.1f}%  {w:3d}W/{l:3d}L  "
                f"n={len(kept):<4d} exp={rr / len(kept):+.2f}R   "
                f"suppressed {supp_w + supp_l:3d} ({supp_l}L / {supp_w}W)  "
                f"R saved {(supp_l * 1.0) - sum(SCALEOUT_R.get(1, 0.5) for _ in range(supp_w)):+.1f}")

        self.stdout.write(self.style.WARNING(
            "\n  Suppressing wins as well as losses is expected and healthy — the test is\n"
            "  whether expectancy and win rate IMPROVE, not whether only losers are cut.\n"
            "  Point-in-time: only stop-outs resolved before each signal was delivered."))
