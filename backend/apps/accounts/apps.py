import logging

from django.apps import AppConfig

logger = logging.getLogger("accounts")


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        # Email verification is hard-required at login. If Resend isn't configured,
        # new signups can never receive their link and would be permanently locked
        # out. Warn loudly rather than crash (a bad email key shouldn't take the whole
        # site down) — the operator can still unblock users with `manage.py
        # verify_user`. Existing users are already backfilled as verified, so this
        # only affects NEW signups.
        from django.conf import settings

        if not settings.RESEND_API_KEY:
            logger.warning(
                "EMAIL VERIFICATION IS ON but RESEND_API_KEY is not set — new signups "
                "cannot receive their verification email and will be unable to log in. "
                "Set RESEND_API_KEY (and verify your sending domain in Resend), or use "
                "`manage.py verify_user` to unblock accounts manually."
            )
