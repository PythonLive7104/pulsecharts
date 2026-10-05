"""Apply the signal universe now (normally a daily Celery task).

    manage.py set_signal_universe --dry-run      # show what would change
    manage.py set_signal_universe                # apply SIGNAL_UNIVERSE_TOP_N
    manage.py set_signal_universe --top 30       # one-off size override

Only Symbol.signals_enabled changes: coins outside the universe stay chartable and
watchlistable, they just stop getting signals. See apps.market_data.universe.
"""

from __future__ import annotations

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.market_data.universe import apply_signal_universe


class Command(BaseCommand):
    help = "Enable signals on the top-N crypto coins by Hyperliquid volume, disable the rest."

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=None,
                            help="Universe size (default SIGNAL_UNIVERSE_TOP_N).")
        parser.add_argument("--dry-run", action="store_true", help="Report only; change nothing.")

    def handle(self, *args, **opts):
        try:
            result = apply_signal_universe(opts["top"], dry_run=opts["dry_run"])
        except (requests.RequestException, ValueError) as exc:
            raise CommandError(f"could not rank coins by volume: {exc}") from None

        if "skipped" in result:
            self.stdout.write(self.style.WARNING(f"Skipped: {result['skipped']}"))
            return

        verb = "Would switch" if result["dry_run"] else "Switched"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Signal universe: top {result['top_n']} by {result['source']} "
            f"({len(result['universe'])} coins)"))
        for i, coin in enumerate(result["universe"], start=1):
            self.stdout.write(f"  {i:>2}. {coin}")
        self.stdout.write(f"{verb} ON  ({len(result['switched_on'])}): "
                          f"{', '.join(result['switched_on']) or '—'}")
        self.stdout.write(f"{verb} OFF ({len(result['switched_off'])}): "
                          f"{', '.join(result['switched_off']) or '—'}")
        if result["too_new"]:
            self.stdout.write(
                f"Skipped, under {result['min_history_days']} days of history: "
                f"{', '.join(result['too_new'])}")
