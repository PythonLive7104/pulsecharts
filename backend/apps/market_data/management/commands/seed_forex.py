"""Seed the 7 major forex pairs (Yahoo Finance tickers).

    python manage.py seed_forex

Unlike crypto (synced live from Hyperliquid via sync_symbols), the forex set is a
deliberately small, curated list of the most liquid majors. `feed_symbol` is the
Yahoo Finance FX ticker (e.g. "EURUSD=X"); `hl_coin` stays blank since forex
doesn't use Hyperliquid. Re-runnable: upserts on ticker, so it won't duplicate
rows.
"""

from django.core.management.base import BaseCommand

from apps.market_data.models import Symbol

# ticker, Yahoo Finance FX ticker, display name
MAJORS = [
    ("EUR-USD", "EURUSD=X", "Euro / US Dollar"),
    ("GBP-USD", "GBPUSD=X", "British Pound / US Dollar"),
    ("USD-JPY", "USDJPY=X", "US Dollar / Japanese Yen"),
    ("USD-CHF", "USDCHF=X", "US Dollar / Swiss Franc"),
    ("AUD-USD", "AUDUSD=X", "Australian Dollar / US Dollar"),
    ("USD-CAD", "USDCAD=X", "US Dollar / Canadian Dollar"),
    ("NZD-USD", "NZDUSD=X", "New Zealand Dollar / US Dollar"),
]

# Forex sorts after crypto in the picker.
_SORT_BASE = 10_000


class Command(BaseCommand):
    help = "Seed the 7 major forex pairs (Twelve Data)."

    def handle(self, *args, **options):
        created = 0
        for i, (ticker, feed, name) in enumerate(MAJORS):
            _, was_created = Symbol.objects.update_or_create(
                ticker=ticker,
                defaults={
                    "asset_class": Symbol.AssetClass.FOREX,
                    "feed_symbol": feed,
                    "hl_coin": "",
                    "display_name": name,
                    "is_active": True,
                    "sort_order": _SORT_BASE + i,
                },
            )
            created += int(was_created)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(MAJORS)} forex majors ({created} new)."
            )
        )
