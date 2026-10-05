"""Long-history archive tests — parsing, range loading, integrity, and the backtest
plumbing that reads it. No network: archives are built in a temp dir."""

import hashlib
import io
import math
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.market_data import history
from apps.market_data.models import Symbol

HOUR = 3600
JAN_2024 = 1704067200  # 2024-01-01 00:00 UTC


def _write_month(root: Path, pair, interval, y, m, rows):
    path = root / pair / interval / f"{pair}-{interval}-{y:04d}-{m:02d}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(path.with_suffix(".csv").name, "\n".join(rows))
    path.write_bytes(buf.getvalue())


def _row(open_ts_s, price, micro=False):
    t = open_ts_s * (1_000_000 if micro else 1_000)
    return f"{t},{price},{price * 1.01},{price * 0.99},{price},10,{t + 1},0,0,0,0,0"


class ParseTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_millis_and_micros_land_on_the_same_clock(self):
        # Binance moved the spot archive to MICROseconds in 2025.
        _write_month(self.root, "BTCUSDT", "1h", 2024, 12, [_row(JAN_2024, 100)])
        _write_month(self.root, "BTCUSDT", "1h", 2025, 1, [_row(JAN_2024 + HOUR, 101, micro=True)])
        candles = history.load("BTCUSDT", "1h", root=self.root)
        self.assertEqual([c["time"] for c in candles], [JAN_2024, JAN_2024 + HOUR])

    def test_header_rows_are_skipped_and_shape_matches_live(self):
        _write_month(self.root, "BTCUSDT", "1h", 2024, 1,
                     ["open_time,open,high,low,close,volume", _row(JAN_2024, 100)])
        (c,) = history.load("BTCUSDT", "1h", root=self.root)
        self.assertEqual(set(c), {"symbol", "interval", "time", "open", "high", "low",
                                  "close", "volume"})

    def test_range_is_start_inclusive_end_exclusive(self):
        _write_month(self.root, "BTCUSDT", "1h", 2024, 1,
                     [_row(JAN_2024 + k * HOUR, 100 + k) for k in range(5)])
        got = history.load("BTCUSDT", "1h", start_ts=JAN_2024 + HOUR,
                           end_ts=JAN_2024 + 3 * HOUR, root=self.root)
        self.assertEqual([c["time"] for c in got], [JAN_2024 + HOUR, JAN_2024 + 2 * HOUR])

    def test_gaps_are_reported_not_filled(self):
        candles = [{"time": JAN_2024}, {"time": JAN_2024 + HOUR}, {"time": JAN_2024 + 4 * HOUR}]
        self.assertEqual(history.gaps(candles, HOUR), [(JAN_2024 + HOUR, 2)])

    def test_pair_for(self):
        self.assertEqual(history.pair_for(Symbol(ticker="BTC", hl_coin="BTC")), "BTCUSDT")
        fx = Symbol(ticker="EUR-USD", hl_coin="", asset_class=Symbol.AssetClass.FOREX)
        self.assertIsNone(history.pair_for(fx))

    def test_checksum_mismatch_is_never_kept(self):
        body = b"not the real zip"

        def fake_get(url, timeout):
            resp = mock.Mock(status_code=200, content=body)
            resp.raise_for_status = lambda: None
            resp.text = ("0" * 64) + "  x.zip"  # wrong hash
            return resp

        session = mock.Mock(get=fake_get)
        with self.assertRaises(history.HistoryError):
            history.download_month("BTCUSDT", "1h", 2024, 1, self.root, session)
        self.assertEqual(list(self.root.rglob("*.zip")), [])

    def test_good_checksum_is_kept_and_then_cached(self):
        body = b"zip-bytes"
        digest = hashlib.sha256(body).hexdigest()

        def fake_get(url, timeout):
            resp = mock.Mock(status_code=200, content=body)
            resp.raise_for_status = lambda: None
            resp.text = f"{digest}  x.zip"
            return resp

        session = mock.Mock(get=fake_get)
        self.assertEqual(history.download_month("BTCUSDT", "1h", 2024, 1, self.root, session),
                         "downloaded")
        self.assertEqual(history.download_month("BTCUSDT", "1h", 2024, 1, self.root, session),
                         "cached")

    def test_connection_reset_is_retried(self):
        import requests

        ok = mock.Mock(status_code=200)
        http = mock.Mock(get=mock.Mock(side_effect=[requests.ConnectionError("reset"), ok]))
        sleeps = []
        self.assertIs(history._get_with_retry(http, "u", 10, sleep=sleeps.append), ok)
        self.assertEqual(sleeps, [history.RETRY_DELAYS[0]])

    def test_rate_limit_is_retried(self):
        limited, ok = mock.Mock(status_code=429), mock.Mock(status_code=200)
        http = mock.Mock(get=mock.Mock(side_effect=[limited, ok]))
        self.assertIs(history._get_with_retry(http, "u", 10, sleep=lambda s: None), ok)

    def test_404_is_not_retried(self):
        missing = mock.Mock(status_code=404)
        http = mock.Mock(get=mock.Mock(return_value=missing))
        self.assertIs(history._get_with_retry(http, "u", 10, sleep=lambda s: None), missing)
        self.assertEqual(http.get.call_count, 1)

    def test_retries_run_out_and_raise(self):
        import requests

        http = mock.Mock(get=mock.Mock(side_effect=requests.ConnectionError("down")))
        with self.assertRaises(requests.ConnectionError):
            history._get_with_retry(http, "u", 10, sleep=lambda s: None)
        self.assertEqual(http.get.call_count, len(history.RETRY_DELAYS) + 1)

    def test_404_is_missing_not_an_error(self):
        session = mock.Mock(get=lambda url, timeout: mock.Mock(status_code=404))
        self.assertEqual(history.download_month("BTCUSDT", "1h", 2030, 1, self.root, session),
                         "missing")


class BacktestHistoryTests(TestCase):
    """The backtest reads the archive end to end: range, warmup and date split."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        Symbol.objects.create(ticker="BTC", hl_coin="BTC")
        call_command("seed_signal_services", stdout=io.StringIO())
        # ~3 months of hourly bars on a slow sine wave: enough for warmup plus trades
        # on both sides of the split, small enough to run in seconds.
        for month, days in ((1, 31), (2, 29), (3, 31)):
            start = JAN_2024 + sum(d for mm, d in ((1, 31), (2, 29)) if mm < month) * 24 * HOUR
            rows = [_row(start + k * HOUR, 100 + 10 * math.sin((start + k * HOUR) / 86400))
                    for k in range(days * 24)]
            _write_month(self.root, "BTCUSDT", "1h", 2024, month, rows)

    def _run(self, *extra):
        out = io.StringIO()
        call_command("backtest", "--history", "--history-dir", str(self.root),
                     "--timeframes", "1h", *extra, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    def test_date_split_reports_both_sides(self):
        out = self._run("--start", "2024-01-20", "--end", "2024-04-01",
                        "--split-date", "2024-03-01")
        self.assertIn("History mode: 2024-01-20 -> 2024-04-01", out)
        self.assertIn("IN-SAMPLE (entered before 2024-03-01)", out)
        self.assertIn("OUT-OF-SAMPLE (entered on/after 2024-03-01)", out)

    def test_results_are_net_of_costs_by_default(self):
        out = self._run("--start", "2024-01-20")
        self.assertIn("Costs: crypto 0.1%", out)

    def test_gross_is_explicit(self):
        out = self._run("--start", "2024-01-20", "--gross")
        self.assertIn("Costs: GROSS", out)

    def test_split_outside_range_is_refused(self):
        with self.assertRaises(CommandError):
            self._run("--start", "2024-02-01", "--split-date", "2024-01-15")

    def test_range_without_history_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("backtest", "--start", "2024-01-01", stdout=io.StringIO())

    def test_no_archive_is_a_clear_error(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        with self.assertRaisesMessage(CommandError, "fetch_history"):
            call_command("backtest", "--history", "--history-dir", str(empty),
                         "--timeframes", "1h", stdout=io.StringIO())
