"""Grant Pro (30 days by default) to every user with Telegram connected, and seed
them the full Pro setup so the signals actually reach them.

The promo this backs: anyone who bothered to link the Telegram bot gets the paid
signal feed free for a month. Connecting Telegram alone doesn't deliver anything —
the feed is filtered by plan quota, watchlist and followed strategies — so the grant
and the provisioning have to happen together.

Two accounts are deliberately skipped:

  * staff/superusers — the admin account, whose plan must not pick up an expiry;
  * anyone on a paid tier with no expiry (lifetime buyers, staff-granted perpetual
    Pro) — ``plans.has_perpetual_access``. Writing a 30-day expiry onto those rows
    would silently downgrade them in a month.

Beyond that it never shortens access: a paying user whose expiry is already past the
promo window keeps that date and is still raised to Pro, so the grant only ever adds.
Idempotent, so it's safe to re-run as more users connect — re-running tops a shorter
grant back up to the full window.

    python manage.py grant_telegram_pro --dry-run      # see who'd be touched
    python manage.py grant_telegram_pro                # grant 30 days of Pro
    python manage.py grant_telegram_pro --all          # EVERY user, Telegram or not
    python manage.py grant_telegram_pro --days 14
    python manage.py grant_telegram_pro --include-disconnected
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.accounts.onboarding import provision_default_setup
from apps.accounts.plans import PAID_TIERS, PRO, has_perpetual_access, plan_key


class Command(BaseCommand):
    help = (
        "Grant Pro for N days (default 30) to users with Telegram connected and seed "
        "them every symbol + strategy. Skips staff and perpetual-access accounts."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Length of the grant in days (default 30).",
        )
        parser.add_argument(
            "--all",
            dest="all_users",
            action="store_true",
            help=(
                "Every user, Telegram or not. Same protections: staff and "
                "perpetual-access accounts never get an expiry."
            ),
        )
        parser.add_argument(
            "--include-disconnected",
            action="store_true",
            help=(
                "Also include users whose chat we still remember but who switched "
                "delivery off (telegram_can_reconnect)."
            ),
        )
        parser.add_argument(
            "--include-staff",
            action="store_true",
            help=(
                "Don't skip staff/superusers. Still skips perpetual-access accounts, "
                "so an admin on non-expiring Pro is never given an expiry."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        days = opts["days"]
        dry = opts["dry_run"]
        granted_until = timezone.now() + timedelta(days=days)

        if opts["all_users"]:
            qs = User.objects.all()
        else:
            qs = User.objects.exclude(telegram_chat_id="")
            if not opts["include_disconnected"]:
                qs = qs.filter(telegram_active=True)
        if not opts["include_staff"]:
            qs = qs.exclude(Q(is_staff=True) | Q(is_superuser=True))
        qs = qs.order_by("id")

        granted = kept = skipped = 0
        total_symbols = total_strategies = 0

        for user in qs.iterator():
            before = plan_key(user)
            # Perpetual paid access: still seeded below, but never stamped with an
            # expiry — that would downgrade them when the promo window closes.
            perpetual = has_perpetual_access(user)
            # Never shorten what they already have: a paid user whose expiry is
            # further out than the promo window keeps that date and is still raised
            # to Pro, so the grant can only ever add access. An expiry sitting on a
            # FREE row is ignored (plan_key doesn't read it, so it's stale data, not
            # access they hold).
            held_until = user.plan_expiry if before in PAID_TIERS else None
            new_expiry = max(held_until, granted_until) if held_until else granted_until
            if perpetual:
                skipped += 1
            else:
                if not dry:
                    user.plan_tier = PRO
                    user.plan_expiry = new_expiry
                    user.save(update_fields=["plan_tier", "plan_expiry"])
                if new_expiry > granted_until:
                    kept += 1
                else:
                    granted += 1

            # Full Pro setup: every active symbol (forex included) + every active
            # built-in strategy. as_plan="pro" sizes the defaults for the grant even
            # on a dry run's unsaved row.
            if perpetual:
                plan_note = f"left on perpetual {before}"
            else:
                plan_note = f"{before} -> pro until {new_expiry:%Y-%m-%d}"
                if new_expiry > granted_until:
                    plan_note += " (kept their longer expiry)"

            if dry:
                self.stdout.write(
                    f"  would touch {user.email}: {plan_note} + full symbols/strategies"
                )
                continue

            result = provision_default_setup(user, as_plan=PRO, include_forex=True)
            total_symbols += result["symbols"]
            total_strategies += result["strategies"]
            self.stdout.write(
                f"  {user.email}: {plan_note} · +{result['symbols']} symbols, "
                f"+{result['strategies']} strategies"
            )

        verb = "Would grant" if dry else "Granted"
        who = "user(s)" if opts["all_users"] else "Telegram-connected user(s)"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {days}-day Pro to {granted} {who}; "
                f"{kept} kept a longer expiry, {skipped} perpetual account(s) "
                f"kept non-expiring. All of them seeded the full Pro symbol set."
            )
        )
        if not dry:
            self.stdout.write(
                f"Seeded {total_symbols} watchlist items and "
                f"{total_strategies} strategy follows."
            )
