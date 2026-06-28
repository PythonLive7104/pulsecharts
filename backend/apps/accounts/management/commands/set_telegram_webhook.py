"""Register (or remove) the Telegram webhook so the bot can receive /start links.

    python manage.py set_telegram_webhook                          # uses FRONTEND_URL
    python manage.py set_telegram_webhook https://your-domain.com
    python manage.py set_telegram_webhook --delete

The URL must be public HTTPS — Telegram won't deliver to http:// or localhost.
Run on every deploy (idempotent); without this the bot never receives /start, so
connect links silently do nothing.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import telegram


class Command(BaseCommand):
    help = "Point the Telegram bot at this app's webhook URL (or remove it with --delete)."

    def add_arguments(self, parser):
        parser.add_argument(
            "base_url",
            nargs="?",
            help="Public base URL, e.g. https://your-domain.com. Defaults to FRONTEND_URL.",
        )
        parser.add_argument("--delete", action="store_true", help="Remove the webhook instead.")

    def handle(self, *args, **opts):
        if not telegram.is_configured():
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in the environment.")

        if opts["delete"]:
            ok = telegram.delete_webhook()
            self.stdout.write(self.style.SUCCESS("Webhook removed.") if ok
                              else self.style.ERROR("Failed to remove webhook."))
            return

        base_url = opts["base_url"] or settings.FRONTEND_URL
        if not base_url or not base_url.startswith("https://"):
            raise CommandError(
                f"Need a public HTTPS base URL (got {base_url!r}). Pass one explicitly "
                "or set FRONTEND_URL to your https:// domain."
            )

        url = f"{base_url.rstrip('/')}/api/telegram/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}/"
        if telegram.set_webhook(url):
            self.stdout.write(self.style.SUCCESS(f"Webhook set:\n  {url}"))
        else:
            raise CommandError("setWebhook failed — check the token and that the URL is public HTTPS.")
