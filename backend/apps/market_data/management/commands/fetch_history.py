"""Download Binance's monthly kline archive for long-history backtests.

    manage.py fetch_history                                  # BTCUSDT 1h,4h,1d since 2018-01
    manage.py fetch_history --pairs BTCUSDT,ETHUSDT --intervals 1h --start 2020-01-01
    manage.py fetch_history --verify                         # re-read what's on disk

Idempotent: months already on disk are skipped, so re-running only fetches what's
new. The month in progress isn't archived by Binance until it ends — that, and
months before a pair listed, report as "missing", which is normal.

Then:  manage.py backtest --history --start 2019-01-01 --split-date 2024-01-01
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.market_data import history


def _bar_seconds(interval: str) -> int:
    return int(interval[:-1]) * {"m": 60, "h": 3600, "d": 86400}[interval[-1]]


class Command(BaseCommand):
    help = "Download Binance monthly kline archives (research data for backtest --history)."

    def add_arguments(self, parser):
        parser.add_argument("--pairs", default="BTCUSDT",
                            help="Comma-separated Binance spot pairs (default BTCUSDT).")
        parser.add_argument("--intervals", default="1h,4h,1d",
                            help="Comma-separated intervals (default 1h,4h,1d). 4h/1d are "
                                 "what the higher-timeframe gates read, so fetch them with 1h.")
        parser.add_argument("--start", default="2018-01-01", help="First month (YYYY-MM-DD).")
        parser.add_argument("--end", default=None,
                            help="Last month (YYYY-MM-DD, default: today; the running month "
                                 "simply reports missing).")
        parser.add_argument("--dir", default=None, help="Archive directory (default HISTORY_DIR).")
        parser.add_argument("--verify", action="store_true",
                            help="Don't download — load what's on disk and report coverage and gaps.")

    def handle(self, *args, **opts):
        pairs = [p.strip().upper() for p in opts["pairs"].split(",") if p.strip()]
        intervals = [i.strip() for i in opts["intervals"].split(",") if i.strip()]
        bad = [i for i in intervals if i not in history.INTERVALS]
        if bad:
            raise CommandError(f"unsupported interval(s): {', '.join(bad)}")
        try:
            start = datetime.fromtimestamp(history.parse_day(opts["start"]), timezone.utc).date()
            end = (datetime.fromtimestamp(history.parse_day(opts["end"]), timezone.utc).date()
                   if opts["end"] else date.today())
        except history.HistoryError as exc:
            raise CommandError(str(exc)) from None
        root = history.history_dir(opts["dir"])

        if not opts["verify"]:
            session = requests.Session()
            for pair in pairs:
                for interval in intervals:
                    counts = {"cached": 0, "downloaded": 0, "missing": 0}
                    for y, m in history.months(start, end):
                        try:
                            status = history.download_month(pair, interval, y, m, root, session)
                        except (requests.RequestException, history.HistoryError) as exc:
                            raise CommandError(f"{pair} {interval} {y}-{m:02d}: {exc}") from None
                        counts[status] += 1
                        if self.stdout.isatty():  # a \r ticker turns to noise in a log
                            self.stdout.write(f"  {pair} {interval} {y}-{m:02d} {status}",
                                              ending="\r")
                    self.stdout.write(
                        f"  {pair} {interval}: {counts['downloaded']} downloaded, "
                        f"{counts['cached']} already on disk, {counts['missing']} not archived"
                        + " " * 10
                    )

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nCoverage in {root}"))
        for pair in pairs:
            for interval in intervals:
                candles = history.load(pair, interval, root=root)
                if not candles:
                    self.stdout.write(self.style.WARNING(f"  {pair} {interval}: no data"))
                    continue
                first = datetime.fromtimestamp(candles[0]["time"], timezone.utc)
                last = datetime.fromtimestamp(candles[-1]["time"], timezone.utc)
                holes = history.gaps(candles, _bar_seconds(interval))
                missing = sum(n for _, n in holes)
                self.stdout.write(
                    f"  {pair} {interval}: {len(candles):,} bars  "
                    f"{first:%Y-%m-%d} -> {last:%Y-%m-%d}  "
                    f"gaps: {len(holes)} ({missing} bars missing — exchange outages, not filled)"
                )
