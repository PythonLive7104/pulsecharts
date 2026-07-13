"""How many stopped-out trades would the trend-break exit have SAVED?

Replays every resolved SL signal bar by bar: recomputes the EMA stack on each candle
after entry and asks which came first —

  * the EMA ordering breaking (9 > 21 > 200 for a BUY stops holding), which
    SIGNAL_EXIT_ON_TREND_BREAK closes flat at 0R, or
  * price reaching the stop loss, which costs -1R.

If the break came first, that trade would have scratched instead of losing. This
measures the exit against real history rather than intuition — and it also counts the
cost, since the same rule fires on WINNING trades too (a trade whose stack broke before
it reached TP1 would have been cut, forfeiting the win).

    python manage.py analyze_trend_break
    python manage.py analyze_trend_break --days 14
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.market_data.feeds import get_candles_since
from apps.signals.indicators import _ema
from apps.signals.models import Signal
from apps.signals.pregate import EMA_STACK_EXEMPT

TP_OUTCOMES = {"TP1", "TP2", "TP3", "TP4"}


def _stack_ok(direction: str, e9, e21, e200) -> bool:
    if e9 is None or e21 is None or e200 is None:
        return True  # not enough history to judge — treat as intact
    return e9 > e21 > e200 if direction == "BUY" else e9 < e21 < e200


class Command(BaseCommand):
    help = "Replay resolved trades: would the trend-break exit have saved them?"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14, help="Lookback window.")

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=opts["days"])
        sigs = list(
            Signal.objects.filter(resolved_at__gte=cutoff, service__owner__isnull=True)
            .exclude(outcome=Signal.Outcome.PENDING)
            .exclude(service__slug__in=EMA_STACK_EXEMPT)
            .select_related("symbol", "service")
        )
        self.stdout.write(f"replaying {len(sigs)} resolved trades from the last {opts['days']}d…\n")

        saved = lost_win = unchanged = skipped = 0

        for s in sigs:
            gen_ms = int(s.generated_at.timestamp() * 1000)
            try:
                # Pull enough history BEFORE entry to seed a 200-period EMA.
                warmup_ms = gen_ms - 250 * 4 * 3600 * 1000
                candles = get_candles_since(s.symbol, s.timeframe, warmup_ms)
            except Exception:
                skipped += 1
                continue
            if len(candles) < 200:
                skipped += 1
                continue

            closes = [c["close"] for c in candles]
            e9, e21, e200 = _ema(closes, 9), _ema(closes, 21), _ema(closes, 200)
            gen_s = s.generated_at.timestamp()

            break_i = stop_i = None
            for i, c in enumerate(candles):
                if c["time"] <= gen_s:
                    continue
                if stop_i is None:
                    hit = (c["low"] <= s.stop_loss) if s.direction == "BUY" else (c["high"] >= s.stop_loss)
                    if hit:
                        stop_i = i
                if break_i is None and not _stack_ok(s.direction, e9[i], e21[i], e200[i]):
                    break_i = i
                if stop_i is not None and break_i is not None:
                    break

            if s.outcome == "SL":
                # Would the exit have fired BEFORE the stop was reached?
                if break_i is not None and (stop_i is None or break_i < stop_i):
                    saved += 1
                else:
                    unchanged += 1
            elif s.outcome in TP_OUTCOMES:
                # The cost side: did the stack break before this winner banked TP1?
                tp1_i = None
                for i, c in enumerate(candles):
                    if c["time"] <= gen_s:
                        continue
                    reached = (c["high"] >= s.tp1) if s.direction == "BUY" else (c["low"] <= s.tp1)
                    if reached:
                        tp1_i = i
                        break
                if break_i is not None and (tp1_i is None or break_i < tp1_i):
                    lost_win += 1
                else:
                    unchanged += 1
            else:
                unchanged += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  SAVED     {saved:4d}  stop-outs that would have closed flat (0R instead of -1R)"))
        self.stdout.write(self.style.WARNING(f"  COST      {lost_win:4d}  winners that would have been cut before TP1"))
        self.stdout.write(f"  unchanged {unchanged:4d}")
        self.stdout.write(f"  skipped   {skipped:4d}  (not enough candle history)")

        net = saved * 1.0 - lost_win * (1 / 3)  # -1R avoided vs +0.33R forfeited
        self.stdout.write("")
        self.stdout.write(
            f"  net R impact: {net:+.2f}R over the window "
            f"(+1R per rescued stop-out, -0.33R per forfeited TP1)"
        )
        if net > 0:
            self.stdout.write(self.style.SUCCESS("  → the exit pays. Keep SIGNAL_EXIT_ON_TREND_BREAK=True."))
        else:
            self.stdout.write(self.style.WARNING("  → the exit costs more than it saves. Consider =False."))
