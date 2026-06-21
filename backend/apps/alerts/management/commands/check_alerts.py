"""Run one price-alert check synchronously (no worker needed).

    python manage.py check_alerts
"""

from django.core.management.base import BaseCommand

from apps.alerts.tasks import run_alert_check


class Command(BaseCommand):
    help = "Check active price alerts against current prices once."

    def handle(self, *args, **options):
        result = run_alert_check()
        self.stdout.write(self.style.SUCCESS(f"Alert check: {result}"))
