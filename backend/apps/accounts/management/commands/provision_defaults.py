"""Backfill default watchlists + followed strategies for existing users.

New users get this automatically at signup (apps.accounts.serializers). This
command applies the same plan-sized defaults to users who already exist. It's
idempotent — only missing symbols/strategies are added — so it's safe to re-run.

    python manage.py provision_defaults                 # all users
    python manage.py provision_defaults --only-empty     # only blank-slate users
    python manage.py provision_defaults --email a@b.com   # a single user
    python manage.py provision_defaults --dry-run
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.onboarding import provision_default_setup


class Command(BaseCommand):
    help = "Provision default watchlist + followed strategies for existing users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-empty",
            action="store_true",
            help="Only users who have no watchlist items AND follow no strategies.",
        )
        parser.add_argument("--email", help="Provision a single user by email.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report who would be provisioned without writing anything.",
        )

    def handle(self, *args, **opts):
        from apps.signals.models import UserSignalSubscription
        from apps.watchlists.models import WatchlistItem

        User = get_user_model()
        qs = User.objects.all().order_by("id")
        if opts.get("email"):
            qs = qs.filter(email__iexact=opts["email"])

        users_touched = 0
        total_symbols = 0
        total_strategies = 0

        for user in qs.iterator():
            if opts["only_empty"]:
                has_watchlist = WatchlistItem.objects.filter(user=user).exists()
                has_strategies = UserSignalSubscription.objects.filter(user=user).exists()
                if has_watchlist or has_strategies:
                    continue

            if opts["dry_run"]:
                self.stdout.write(f"would provision {user.email} ({user.plan_key})")
                users_touched += 1
                continue

            result = provision_default_setup(user)
            if result["symbols"] or result["strategies"]:
                users_touched += 1
                total_symbols += result["symbols"]
                total_strategies += result["strategies"]
                self.stdout.write(
                    f"{user.email} ({user.plan_key}): "
                    f"+{result['symbols']} symbols, +{result['strategies']} strategies"
                )

        verb = "Would provision" if opts["dry_run"] else "Provisioned"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {users_touched} user(s); "
                f"added {total_symbols} watchlist items, {total_strategies} strategy follows."
            )
        )
