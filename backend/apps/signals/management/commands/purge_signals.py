"""Purge resolved signals and seen alerts past the retention window.

Frees database space by deleting old data that's no longer needed. Open (PENDING)
signals are always kept. Runs daily via Celery Beat (purge_old_data); this command
is for manual / one-off runs.

    python manage.py purge_signals            # use SIGNAL_RETENTION_DAYS
    python manage.py purge_signals --days 7   # override the window
    python manage.py purge_signals --all      # wipe ALL signals (clean reset)

Note: the default/retention modes only remove *resolved* signals older than the
window — recent or still-open calls are kept. Use --all to fully reset the
signal history (e.g. starting shadow-mode validation on a new config).
"""

from django.core.management.base import BaseCommand

from apps.signals.tasks import run_purge


class Command(BaseCommand):
    help = "Delete resolved signals + seen alerts older than the retention window (or all with --all)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="Retention window in days.")
        parser.add_argument(
            "--all", action="store_true", dest="purge_all",
            help="Delete EVERY signal, including open/pending — full reset.",
        )

    def handle(self, *args, **opts):
        if opts["purge_all"]:
            from apps.signals.models import Signal

            total, _ = Signal.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(
                    f"Deleted ALL signals — {total} rows removed (incl. deliveries). "
                    "Signal history reset."
                )
            )
            return

        result = run_purge(days=opts["days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {result['signals_deleted']} signal rows and "
                f"{result['alerts_deleted']} alert rows (older than {result['days']}d)."
            )
        )
