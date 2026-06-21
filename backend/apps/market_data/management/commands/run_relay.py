"""Run the Hyperliquid -> Channels relay as a long-lived process.

    python manage.py run_relay

Deploy this as a separate worker process alongside the ASGI server.
"""

import asyncio
import logging

from django.core.management.base import BaseCommand

from apps.market_data.relay import run_relay_forever


class Command(BaseCommand):
    help = "Run the Hyperliquid market-data relay (Section 7)."

    def handle(self, *args, **options):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.stdout.write(self.style.SUCCESS("Starting Hyperliquid relay…"))
        try:
            asyncio.run(run_relay_forever())
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Relay stopped."))
