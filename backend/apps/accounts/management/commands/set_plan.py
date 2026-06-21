"""Set a user's plan tier (and expiry) from the CLI.

Useful for granting yourself/admins a plan without clicking through the admin
(and without the easy mistake of leaving a past plan_expiry, which silently
downgrades a paid tier back to Free — see apps.accounts.plans.plan_key).

  # perpetual Pro (no expiry — the right call for an owner/admin account)
  python manage.py set_plan you@example.com pro

  # 30-day Starter
  python manage.py set_plan friend@example.com starter --days 30
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.plans import plan_key


class Command(BaseCommand):
    help = "Set a user's plan tier and expiry. Omit --days for a perpetual plan."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("tier", choices=["free", "starter", "pro"])
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Days until the plan expires. Omit for a perpetual plan (no expiry).",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=opts["email"])
        except User.DoesNotExist:
            raise CommandError(f"No user with email {opts['email']!r}")

        before = plan_key(user)
        user.plan_tier = opts["tier"]
        user.plan_expiry = (
            None if opts["days"] is None else timezone.now() + timedelta(days=opts["days"])
        )
        user.save(update_fields=["plan_tier", "plan_expiry"])

        self.stdout.write(
            self.style.SUCCESS(
                f"{user.email}: effective plan {before} -> {plan_key(user)} "
                f"(tier={user.plan_tier}, expiry={user.plan_expiry or 'never'})"
            )
        )
