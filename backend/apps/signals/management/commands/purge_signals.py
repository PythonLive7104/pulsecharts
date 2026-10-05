"""Purge resolved signals and seen alerts past the retention window.

Frees database space by deleting old data that's no longer needed. Open (PENDING)
signals are always kept, and so is anything a user was delivered or traded — that
is the permanent track record (tasks.never_delivered). Runs daily via Celery Beat
(purge_old_data); this command is for manual / one-off runs.

    python manage.py purge_signals            # use SIGNAL_RETENTION_DAYS
    python manage.py purge_signals --days 7   # override the window
    python manage.py purge_signals --all      # wipe every UNDELIVERED signal
    python manage.py purge_signals --all --include-delivered   # true wipe (dev only)

Note: the default/retention modes only remove *resolved, undelivered* signals older
than the window — recent, still-open or delivered calls are kept. Use --all to reset
the scan's working output (e.g. starting shadow-mode validation on a new config).
--include-delivered also erases the delivered record; it exists for a local dev
database and should never be run against production.
"""

from django.core.management.base import BaseCommand

from apps.signals.tasks import never_delivered, run_purge


class Command(BaseCommand):
    help = "Delete resolved, undelivered signals + seen alerts older than the retention window (or all undelivered with --all)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="Retention window in days.")
        parser.add_argument(
            "--flat-days", type=int, default=None,
            help="Shorter window for INVALIDATED/EXPIRED calls, which carry no result "
                 "and are excluded from the win rate either way (live: "
                 "SIGNAL_RETENTION_DAYS_FLAT). Clamped to --days.",
        )
        parser.add_argument(
            "--all", action="store_true", dest="purge_all",
            help="Delete EVERY undelivered signal, including open/pending — full reset "
                 "of the scan output. Delivered signals are kept.",
        )
        parser.add_argument(
            "--include-delivered", action="store_true",
            help="With --all: also delete delivered/traded signals, erasing the track "
                 "record. Dev databases only.",
        )

    def handle(self, *args, **opts):
        if opts["purge_all"]:
            from apps.signals.models import Signal

            if opts["include_delivered"]:
                total, _ = Signal.objects.all().delete()
                self.stdout.write(
                    self.style.WARNING(
                        f"Deleted ALL signals — {total} rows removed (incl. deliveries). "
                        "Signal history AND track record reset."
                    )
                )
                return

            total, _ = never_delivered(Signal.objects.all()).delete()
            kept = Signal.objects.count()
            self.stdout.write(
                self.style.WARNING(
                    f"Deleted every undelivered signal — {total} rows removed. "
                    f"Kept {kept} delivered/traded signal(s): the track record."
                )
            )
            return

        if opts["include_delivered"]:
            self.stderr.write("--include-delivered only applies together with --all.")
            return

        result = run_purge(days=opts["days"], flat_days=opts["flat_days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {result['signals_deleted']} undelivered signal rows "
                f"({result['flat_deleted']} invalidated/expired at {result['flat_days']}d) "
                f"and {result['alerts_deleted']} alert rows (older than {result['days']}d). "
                "Delivered signals are never purged."
            )
        )
