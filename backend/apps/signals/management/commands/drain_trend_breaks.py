"""One-time quiet drain of the open-trade backlog when enabling the trend-break exit.

Turning on SIGNAL_EXIT_ON_TREND_BREAK makes the next scan invalidate EVERY open call
whose EMA stack has already broken. After days of accumulation that is hundreds of
trades closing in one pass — and each one that was delivered would fire a Telegram
"invalidated" notice, burying the user and tripping Telegram's rate limits.

This command does that first invalidation sweep with the notifications SUPPRESSED: the
trades are closed correctly (so the stats and the freed slots are right), but their
Telegram closure notices are marked as already-sent. Only the backlog is silenced —
every trade invalidated from here on notifies normally.

Run it ONCE, with beat/worker stopped so the scan can't race it:

    docker compose stop beat worker
    docker compose exec web python manage.py drain_trend_breaks
    docker compose start beat worker
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.market_data.feeds import get_candles
from apps.market_data.models import Symbol
from apps.signals.indicators import compute_indicators
from apps.signals.models import Signal, TelegramDelivery
from apps.signals.tasks import MIN_CANDLES, _invalidate_trend_breaks


class Command(BaseCommand):
    help = "Invalidate already-broken open trades WITHOUT sending Telegram notices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without closing anything."
        )

    def handle(self, *args, **opts):
        now = timezone.now()
        # Distinct (symbol, timeframe) pairs that actually have open calls.
        pairs = (
            Signal.objects.filter(outcome=Signal.Outcome.PENDING, best_tp=0)
            .values_list("symbol_id", "timeframe")
            .distinct()
        )
        pairs = list(pairs)
        self.stdout.write(f"checking {len(pairs)} (symbol, timeframe) pairs with open calls…")

        symbols = {s.id: s for s in Symbol.objects.filter(id__in={p[0] for p in pairs})}
        before = set(
            Signal.objects.filter(outcome=Signal.Outcome.PENDING).values_list("id", flat=True)
        )

        closed = 0
        for sym_id, tf in pairs:
            sym = symbols.get(sym_id)
            if not sym:
                continue
            try:
                candles = get_candles(sym, tf, limit=300)
            except Exception:
                continue
            if len(candles) < MIN_CANDLES:
                continue
            ind = compute_indicators(candles)
            if opts["dry_run"]:
                e9, e21, e200 = ind.get("ema9"), ind.get("ema21"), ind.get("ema200")
                if None in (e9, e21, e200):
                    continue
                n = Signal.objects.filter(
                    symbol=sym, timeframe=tf, outcome=Signal.Outcome.PENDING, best_tp=0,
                    service__owner__isnull=True,
                ).count()
                if n and not (e9 > e21 > e200):
                    closed += n  # rough: counts both directions' candidates
                continue
            closed += _invalidate_trend_breaks(sym, tf, ind, now)

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING(f"~{closed} open call(s) would be invalidated."))
            return

        # Silence ONLY the closures we just created.
        just_closed = list(
            Signal.objects.filter(
                id__in=before, outcome=Signal.Outcome.INVALIDATED
            ).values_list("id", flat=True)
        )
        muted = TelegramDelivery.objects.filter(
            signal_id__in=just_closed, closure_notified=False
        ).update(closure_notified=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Invalidated {closed} open call(s); suppressed {muted} Telegram notice(s). "
                "Future invalidations will notify normally."
            )
        )
