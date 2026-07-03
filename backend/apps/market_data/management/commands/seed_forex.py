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

# ticker, Yahoo Finance ticker, display name, min_plan
# The 7 majors are free. The minor crosses (added on demand) are split: the four
# headline crosses stay free so the free tier still feels complete, while the
# deeper crosses are gated to Pro — this doubles as a Yahoo throttle, since fewer
# users pull the Pro set live. Gold (XAU-USD) rides the same Yahoo/forex pipeline
# but is gated to Pro — note Yahoo serves gold as the COMEX future "GC=F"
# ("XAUUSD=X" is not listed), which tracks spot closely enough for charting.
# All Yahoo tickers below were curl-verified to return clean candles before seeding.
MAJORS = [
    # --- Majors (free) ---
    ("EUR-USD", "EURUSD=X", "Euro / US Dollar", Symbol.MinPlan.FREE),
    ("GBP-USD", "GBPUSD=X", "British Pound / US Dollar", Symbol.MinPlan.FREE),
    ("USD-JPY", "USDJPY=X", "US Dollar / Japanese Yen", Symbol.MinPlan.FREE),
    ("USD-CHF", "USDCHF=X", "US Dollar / Swiss Franc", Symbol.MinPlan.FREE),
    ("AUD-USD", "AUDUSD=X", "Australian Dollar / US Dollar", Symbol.MinPlan.FREE),
    ("USD-CAD", "USDCAD=X", "US Dollar / Canadian Dollar", Symbol.MinPlan.FREE),
    ("NZD-USD", "NZDUSD=X", "New Zealand Dollar / US Dollar", Symbol.MinPlan.FREE),
    # --- Headline crosses (free) ---
    ("EUR-GBP", "EURGBP=X", "Euro / British Pound", Symbol.MinPlan.FREE),
    ("EUR-JPY", "EURJPY=X", "Euro / Japanese Yen", Symbol.MinPlan.FREE),
    ("GBP-JPY", "GBPJPY=X", "British Pound / Japanese Yen", Symbol.MinPlan.FREE),
    ("AUD-JPY", "AUDJPY=X", "Australian Dollar / Japanese Yen", Symbol.MinPlan.FREE),
    # --- Deeper crosses (Pro) ---
    ("EUR-CHF", "EURCHF=X", "Euro / Swiss Franc (Pro)", Symbol.MinPlan.PRO),
    ("EUR-AUD", "EURAUD=X", "Euro / Australian Dollar (Pro)", Symbol.MinPlan.PRO),
    ("EUR-CAD", "EURCAD=X", "Euro / Canadian Dollar (Pro)", Symbol.MinPlan.PRO),
    ("GBP-CHF", "GBPCHF=X", "British Pound / Swiss Franc (Pro)", Symbol.MinPlan.PRO),
    ("GBP-AUD", "GBPAUD=X", "British Pound / Australian Dollar (Pro)", Symbol.MinPlan.PRO),
    ("CAD-JPY", "CADJPY=X", "Canadian Dollar / Japanese Yen (Pro)", Symbol.MinPlan.PRO),
    ("CHF-JPY", "CHFJPY=X", "Swiss Franc / Japanese Yen (Pro)", Symbol.MinPlan.PRO),
    ("NZD-JPY", "NZDJPY=X", "New Zealand Dollar / Japanese Yen (Pro)", Symbol.MinPlan.PRO),
    ("AUD-NZD", "AUDNZD=X", "Australian Dollar / New Zealand Dollar (Pro)", Symbol.MinPlan.PRO),
    ("AUD-CAD", "AUDCAD=X", "Australian Dollar / Canadian Dollar (Pro)", Symbol.MinPlan.PRO),
    # --- Metals (Pro) ---
    ("XAU-USD", "GC=F", "Gold / US Dollar (Pro)", Symbol.MinPlan.PRO),
]

# Forex sorts after crypto in the picker.
_SORT_BASE = 10_000


class Command(BaseCommand):
    help = "Seed the major forex pairs + gold (Yahoo Finance)."

    def handle(self, *args, **options):
        created = 0
        for i, (ticker, feed, name, min_plan) in enumerate(MAJORS):
            _, was_created = Symbol.objects.update_or_create(
                ticker=ticker,
                defaults={
                    "asset_class": Symbol.AssetClass.FOREX,
                    "feed_symbol": feed,
                    "hl_coin": "",
                    "display_name": name,
                    "is_active": True,
                    "sort_order": _SORT_BASE + i,
                    "min_plan": min_plan,
                },
            )
            created += int(was_created)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(MAJORS)} forex/metal symbols ({created} new)."
            )
        )
