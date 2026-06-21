"""Register (or remove) the Telegram webhook so the bot can receive /start links.

    python manage.py set_telegram_webhook https://your-domain.com
    python manage.py set_telegram_webhook https://your-domain.com --delete

The URL must be public HTTPS — Telegram won't deliver to http:// or localhost.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import telegram


class Command(BaseCommand):
    help = "Point the Telegram bot at this app's webhook URL (or remove it with --delete)."

    def add_arguments(self, parser):
        parser.add_argument("base_url", help="Public base URL, e.g. https://your-domain.com")
        parser.add_argument("--delete", action="store_true", help="Remove the webhook instead.")

    def handle(self, *args, **opts):
        if not telegram.is_configured():
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in the environment.")

        if opts["delete"]:
            ok = telegram.delete_webhook()
            self.stdout.write(self.style.SUCCESS("Webhook removed.") if ok
                              else self.style.ERROR("Failed to remove webhook."))
            return

        url = f"{opts['base_url'].rstrip('/')}/api/telegram/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}/"
        if telegram.set_webhook(url):
            self.stdout.write(self.style.SUCCESS(f"Webhook set:\n  {url}"))
        else:
            raise CommandError("setWebhook failed — check the token and that the URL is public HTTPS.")
