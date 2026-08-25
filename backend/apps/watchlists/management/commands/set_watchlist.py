"""Replace a user's watchlist with an explicit symbol list.

The watchlist is the scope of a user's signal feed (``signals.views`` filters the
feed by it, and ``signals.tasks`` scans the union of all watchlists), and onboarding
seeds it to the plan maximum — 100+ coins on Pro. That is right for normal use and
wrong for measuring: a feed spread across 100 symbols resolves too few trades per
symbol to tell a good coin from a bad one inside a reasonable window.

Narrowing to 10-20 concentrates the same signal budget, so a live window produces
enough resolved trades per symbol to read. Trimming 90 coins one click at a time in
the UI is the only alternative.

    manage.py set_watchlist --email me@example.com --symbols BTC-USD,ETH-USD,SOL-USD
    manage.py set_watchlist --email me@example.com --symbols @majors --dry-run

Prints the diff and asks nothing else; --dry-run shows it without writing.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.market_data.models import Symbol
from apps.watchlists.models import WatchlistItem, watchlist_limit_for

# Named sets, so a focused live test doesn't depend on retyping a list correctly.
# Deliberately the most liquid Hyperliquid perps: thin books widen the spread the
# signal has to clear, which is a property of the venue rather than the strategy.
PRESETS = {
    "majors": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
               "DOGE-USD", "AVAX-USD", "LTC-USD", "LINK-USD", "ADA-USD"],
    "majors20": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
                 "DOGE-USD", "AVAX-USD", "LTC-USD", "LINK-USD", "ADA-USD",
                 "ARB-USD", "OP-USD", "ATOM-USD", "APT-USD", "SUI-USD",
                 "NEAR-USD", "INJ-USD", "TIA-USD", "DYDX-USD", "MATIC-USD"],
}


class Command(BaseCommand):
    help = "Replace a user's watchlist with an explicit symbol list (for focused live tests)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True,
                            help="The user whose watchlist is replaced.")
        parser.add_argument("--symbols", required=True,
                            help="Comma-separated tickers (BTC-USD,ETH-USD), or "
                                 "@majors / @majors20 for a preset.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Show the diff without writing.")

    def handle(self, *args, **opts):
        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=opts["email"])
        except User.DoesNotExist:
            raise CommandError(f"No user with email {opts['email']!r}")

        raw = opts["symbols"].strip()
        if raw.startswith("@"):
            preset = raw[1:]
            if preset not in PRESETS:
                raise CommandError(
                    f"Unknown preset {raw!r}. Available: "
                    + ", ".join(f"@{k}" for k in PRESETS))
            wanted = list(PRESETS[preset])
        else:
            wanted = [t.strip().upper() for t in raw.split(",") if t.strip()]
        if not wanted:
            raise CommandError("No symbols given.")

        # Deduplicate while preserving the order given — that order becomes sort_order.
        seen, ordered = set(), []
        for t in wanted:
            if t not in seen:
                seen.add(t)
                ordered.append(t)

        found = {s.ticker: s for s in Symbol.objects.filter(ticker__in=ordered)}
        missing = [t for t in ordered if t not in found]
        if missing:
            # Hard error rather than a silent partial write: a typo'd ticker would
            # otherwise leave a watchlist quietly shorter than intended, and the
            # whole point here is knowing exactly what is being measured.
            raise CommandError(
                "Unknown ticker(s): " + ", ".join(missing)
                + "\nCheck spelling against `Symbol` (e.g. BTC-USD, not BTC).")

        inactive = [t for t in ordered if not found[t].is_active]
        if inactive:
            self.stdout.write(self.style.WARNING(
                "  ! inactive symbol(s), they will not produce signals: "
                + ", ".join(inactive)))

        limit = watchlist_limit_for(user)
        if len(ordered) > limit:
            raise CommandError(
                f"{len(ordered)} symbols exceeds this user's plan limit of {limit}.")

        current = set(
            WatchlistItem.objects.filter(user=user)
            .values_list("symbol__ticker", flat=True)
        )
        adding = [t for t in ordered if t not in current]
        removing = sorted(current - set(ordered))

        self.stdout.write(f"\n  user     : {user.email}")
        self.stdout.write(f"  current  : {len(current)} symbols")
        self.stdout.write(f"  target   : {len(ordered)} symbols  (plan limit {limit})")
        self.stdout.write(f"  adding   : {', '.join(adding) or '—'}")
        self.stdout.write(f"  removing : {len(removing)} "
                          f"({', '.join(removing[:12])}{'…' if len(removing) > 12 else ''})")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("\n  --dry-run: nothing written.\n"))
            return

        with transaction.atomic():
            WatchlistItem.objects.filter(user=user).delete()
            WatchlistItem.objects.bulk_create([
                WatchlistItem(user=user, symbol=found[t], sort_order=i)
                for i, t in enumerate(ordered)
            ])

        self.stdout.write(self.style.SUCCESS(
            f"\n  ✓ watchlist set to {len(ordered)} symbols.\n"))
        self.stdout.write(
            "  Existing open signals for removed coins stay in the feed until they\n"
            "  resolve — this only changes what NEW signals you are delivered.\n")
