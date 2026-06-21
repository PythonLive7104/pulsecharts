"""Sync the Symbol table with Hyperliquid's live perp universe (Section 8, 16).

    python manage.py sync_symbols          # add/update perps
    python manage.py sync_symbols --prune  # also deactivate coins no longer listed

Pulls every perpetual from Hyperliquid's `info` endpoint (type "meta") and
upserts a Symbol row per coin. This keeps coverage in step with what's actually
tradable rather than a hand-maintained list. Perps only (per the product
decision) — spot pairs are intentionally skipped.

Coins that are delisted (or, with --prune, no longer in the universe at all) are
marked is_active=False rather than deleted, so existing watchlist/layout rows
that reference them stay intact.
"""

import requests
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.market_data.client import fetch_perp_universe
from apps.market_data.models import Symbol


class Command(BaseCommand):
    help = "Populate/refresh the Symbol table from Hyperliquid's perp universe."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Deactivate symbols that are no longer in the live universe.",
        )

    def handle(self, *args, **options):
        try:
            universe = fetch_perp_universe()
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f"Failed to reach Hyperliquid: {exc}"))
            return

        if not universe:
            self.stderr.write(self.style.WARNING("Empty universe returned; nothing to do."))
            return

        deactivated = 0
        seen_coins = set()

        # Build all rows, then upsert in ONE query (INSERT ... ON CONFLICT). The
        # old row-by-row update_or_create did ~2 round-trips per coin, which is
        # slow-to-minutes against a remote DB (e.g. Supabase); bulk_create with
        # update_conflicts collapses it to a single statement.
        rows = []
        for i, entry in enumerate(universe):
            coin = entry.get("name")
            if not coin:
                continue
            seen_coins.add(coin)
            rows.append(Symbol(
                ticker=f"{coin}-USD",
                hl_coin=coin,
                display_name=coin,
                is_active=not entry.get("isDelisted", False),
                sort_order=i,
            ))

        existing = set(Symbol.objects.values_list("ticker", flat=True))
        created = sum(1 for r in rows if r.ticker not in existing)
        updated = len(rows) - created

        with transaction.atomic():
            Symbol.objects.bulk_create(
                rows,
                update_conflicts=True,
                unique_fields=["ticker"],
                update_fields=["hl_coin", "display_name", "is_active", "sort_order"],
            )

            if options["prune"]:
                stale = Symbol.objects.filter(is_active=True).exclude(hl_coin__in=seen_coins)
                deactivated = stale.update(is_active=False)

        total_active = Symbol.objects.filter(is_active=True).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {len(seen_coins)} perps "
                f"({created} new, {updated} updated, {deactivated} deactivated). "
                f"{total_active} active symbols total."
            )
        )
