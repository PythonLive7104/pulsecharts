from datetime import datetime, timezone

from django.test import SimpleTestCase

from apps.signals.tasks import _closed_candles


class ClosedCandleTests(SimpleTestCase):
    def test_excludes_current_bar_until_close(self):
        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        candles = [
            {"time": int(datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc).timestamp())},
            {"time": int(datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc).timestamp())},
        ]

        self.assertEqual(_closed_candles(candles, "4h", now=now), candles[:1])

    def test_includes_bar_at_its_close_boundary(self):
        now = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
        candle = {"time": int(datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc).timestamp())}

        self.assertEqual(_closed_candles([candle], "4h", now=now), [candle])

    def test_daily_bar_remains_unavailable_until_full_day_closes(self):
        start = int(datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc).timestamp())
        candle = {"time": start}

        self.assertEqual(
            _closed_candles([candle], "1d", now=datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)),
            [],
        )