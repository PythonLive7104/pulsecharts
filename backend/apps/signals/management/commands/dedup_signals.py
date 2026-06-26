"""Collapse duplicate open signals.

Before direction-aware dedup landed, a scan could create a fresh Signal for the
same (symbol, strategy, timeframe) on every run, leaving several identical open
calls. This command enforces the new invariant retroactively: for each
(symbol, service, timeframe) it keeps the most recent PENDING signal and deletes
the older duplicates. Timeframe is part of the key — a 1h and a 4h call on the
same symbol/strategy are different trades, not duplicates.

    python manage.py dedup_signals          # dry run (shows what it would remove)
    python manage.py dedup_signals --apply  # actually delete the duplicates
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.signals.models import Signal


class Command(BaseCommand):
    help = "Remove duplicate open (PENDING) signals, keeping the newest per symbol+strategy."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete duplicates (default: dry run).")

    def handle(self, *args, **opts):
        pending = (
            Signal.objects.filter(outcome=Signal.Outcome.PENDING)
            .select_related("symbol", "service")
            .order_by("-generated_at")  # newest first → first seen per pair is the keeper
        )

        groups = defaultdict(list)
        for sig in pending:
            groups[(sig.symbol_id, sig.service_id, sig.timeframe)].append(sig)

        dupe_ids = []
        for (sym_id, svc_id, tf), sigs in groups.items():
            if len(sigs) <= 1:
                continue
            keeper, *dupes = sigs  # keeper is the newest
            label = f"{keeper.symbol.ticker} {tf} / {keeper.service.name}"
            self.stdout.write(
                f"{label}: keep #{keeper.id} ({keeper.generated_at:%Y-%m-%d %H:%M}), "
                f"remove {len(dupes)} older"
            )
            dupe_ids.extend(s.id for s in dupes)

        if not dupe_ids:
            self.stdout.write(self.style.SUCCESS("No duplicate open signals found."))
            return

        if not opts["apply"]:
            self.stdout.write(
                self.style.WARNING(f"\nDry run: {len(dupe_ids)} duplicates would be deleted. Re-run with --apply.")
            )
            return

        deleted, _ = Signal.objects.filter(id__in=dupe_ids).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {len(dupe_ids)} duplicate signals ({deleted} rows incl. deliveries)."))
