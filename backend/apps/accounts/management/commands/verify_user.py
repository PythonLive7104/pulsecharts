"""Manually mark an account's email as verified — the operator escape hatch.

Email verification is hard-required at login. If a user can't receive the email
(delivery down, wrong address, Resend misconfigured), this lets you unblock them
without touching the database by hand.

    python manage.py verify_user someone@example.com
    python manage.py verify_user --all-unverified          # unblock everyone stuck
    python manage.py verify_user someone@example.com --resend   # re-send instead
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

User = get_user_model()


class Command(BaseCommand):
    help = "Mark a user's email verified (or resend their verification link)."

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?", help="Account email to verify.")
        parser.add_argument(
            "--all-unverified",
            action="store_true",
            help="Verify EVERY currently-unverified account (use if email was broken).",
        )
        parser.add_argument(
            "--resend",
            action="store_true",
            help="Re-send the verification email instead of force-verifying.",
        )

    def handle(self, *args, **opts):
        if opts["all_unverified"]:
            qs = User.objects.filter(email_verified=False)
            n = qs.update(email_verified=True, email_verified_at=timezone.now())
            self.stdout.write(self.style.SUCCESS(f"Verified {n} previously-unverified account(s)."))
            return

        email = opts.get("email")
        if not email:
            raise CommandError("Provide an email, or use --all-unverified.")
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise CommandError(f"No account with email {email!r}.")

        if opts["resend"]:
            from apps.accounts.verification import send_verification

            sent = send_verification(user)
            self.stdout.write(
                self.style.SUCCESS(f"Verification email {'sent' if sent else 'logged (email disabled)'} to {email}.")
            )
            return

        if user.email_verified:
            self.stdout.write(f"{email} is already verified.")
            return
        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified", "email_verified_at"])
        self.stdout.write(self.style.SUCCESS(f"Verified {email}."))
