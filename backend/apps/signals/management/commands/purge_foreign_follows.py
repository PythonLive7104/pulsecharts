"""Remove subscriptions to OTHER users' private custom strategies.

Custom strategies are private to their owner, but onboarding's "follow every active
strategy" (Pro: default_strategies = -1) did not filter by owner, so provisioning a
user subscribed them to every custom strategy in the table — and the feed and the
Telegram push then delivered those private signals to them.

The code paths are fixed (onboarding._ordered_active_services,
views._followed_service_ids, tasks.run_telegram_push), but the bad subscription rows
already written have to be deleted. Idempotent, and safe to re-run.

    python manage.py purge_foreign_follows --dry-run
    python manage.py purge_foreign_follows
"""

from django.core.management.base import BaseCommand
from django.db.models import F

from apps.signals.models import SignalDelivery, TelegramDelivery, UserSignalSubscription


class Command(BaseCommand):
    help = "Delete subscriptions to (and deliveries of) custom strategies the user doesn't own."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the offending follows without deleting anything.",
        )

    def handle(self, *args, **opts):
        # A follow is foreign when the service HAS an owner and it isn't the subscriber.
        bad = (
            UserSignalSubscription.objects.filter(service__owner__isnull=False)
            .exclude(service__owner=F("user"))
            .select_related("user", "service", "service__owner")
        )

        # The signals already handed over. These back the user's feed, past results and
        # accuracy stats, so leaving them behind would keep another user's private
        # signals in the victim's history even after the follow is gone.
        bad_feed = SignalDelivery.objects.filter(
            signal__service__owner__isnull=False
        ).exclude(signal__service__owner=F("user"))
        bad_tg = TelegramDelivery.objects.filter(
            signal__service__owner__isnull=False
        ).exclude(signal__service__owner=F("user"))

        rows = list(bad)
        n_feed, n_tg = bad_feed.count(), bad_tg.count()

        if not rows and not n_feed and not n_tg:
            self.stdout.write(self.style.SUCCESS("No foreign follows or deliveries found."))
            return

        for s in rows:
            self.stdout.write(
                f"  {s.user.email} follows '{s.service.name}' "
                f"(owned by {s.service.owner.email})"
            )
        self.stdout.write(
            f"  leaked deliveries: {n_feed} in-app, {n_tg} telegram"
        )

        if opts["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Would delete {len(rows)} follow(s), {n_feed} in-app delivery/ies, "
                    f"{n_tg} telegram delivery/ies."
                )
            )
            return

        bad_feed.delete()
        bad_tg.delete()
        n, _ = bad.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {n} foreign follow(s), {n_feed} in-app + {n_tg} telegram deliveries."
            )
        )
