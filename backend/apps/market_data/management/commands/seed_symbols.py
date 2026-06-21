"""Seed a starter set of Hyperliquid symbols for local dev.

    python manage.py seed_symbols

Section 16 caveat: confirm every ticker is actually listed on Hyperliquid before
relying on this set in production. These are common perps used for scaffolding.
"""

from django.core.management.base import BaseCommand

from apps.market_data.models import Symbol

STARTER = [
    ("BTC-USD", "BTC", "Bitcoin"),
    ("ETH-USD", "ETH", "Ethereum"),
    ("SOL-USD", "SOL", "Solana"),
    ("ARB-USD", "ARB", "Arbitrum"),
    ("AVAX-USD", "AVAX", "Avalanche"),
]


class Command(BaseCommand):
    help = "Seed a starter set of symbols."

    def handle(self, *args, **options):
        created = 0
        for i, (ticker, coin, name) in enumerate(STARTER):
            _, was_created = Symbol.objects.update_or_create(
                ticker=ticker,
                defaults={"hl_coin": coin, "display_name": name, "sort_order": i},
            )
            created += int(was_created)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(STARTER)} symbols ({created} new)."
            )
        )
