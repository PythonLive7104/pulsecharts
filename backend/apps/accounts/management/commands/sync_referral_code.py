"""Sync the admin/owner referral code from settings.ADMIN_REFERRAL_CODE to the DB.

Run on startup (dev.sh / update.sh / the web container) so editing
ADMIN_REFERRAL_CODE in .env updates the live code:

    python manage.py sync_referral_code

The env code is the special one that grants new signups a 30-day Starter plan
(grants_signup_plan=True) and, if a superuser exists, credits that superuser $1
per referral. There is exactly one such env-managed code (marked internally);
changing the .env value renames it in place.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import PlanTier, ReferralCode

MARKER = "env-admin-code"  # identifies the single env-managed admin code


class Command(BaseCommand):
    help = "Create/update the admin referral code from settings.ADMIN_REFERRAL_CODE."

    def handle(self, *args, **opts):
        code = (settings.ADMIN_REFERRAL_CODE or "").strip().upper()
        if not code:
            self.stdout.write("ADMIN_REFERRAL_CODE is empty — nothing to sync.")
            return

        User = get_user_model()
        owner = User.objects.filter(is_superuser=True).order_by("id").first()

        # If the desired code is already used by a *different* (non-admin) code,
        # refuse rather than silently hijack it.
        clash = ReferralCode.objects.filter(code=code).exclude(note=MARKER).first()
        if clash:
            self.stderr.write(self.style.ERROR(
                f"'{code}' is already taken by another referral code (id={clash.id}). "
                "Pick a different ADMIN_REFERRAL_CODE."
            ))
            return

        rc = ReferralCode.objects.filter(note=MARKER).first() or ReferralCode(note=MARKER)
        rc.code = code
        rc.grants_signup_plan = True
        rc.grant_tier = PlanTier.STARTER
        rc.grant_days = 30
        rc.is_active = True
        rc.owner = owner
        rc.save()

        self.stdout.write(self.style.SUCCESS(
            f"Admin referral code synced: {rc.code} — grants 30-day Starter; "
            f"owner={owner.email if owner else '(no superuser yet)'}."
        ))
