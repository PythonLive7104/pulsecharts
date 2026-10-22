"""Proactive pacing of Hyperliquid REST calls.

The client's retry/backoff is reactive — it reacts after a request has already been
refused. A scan fires one call per symbol per timeframe plus the higher-timeframe
reads, ~300 back to back at 70 symbols, and in production that produced bursts of
429s where four coins exhausted all three retries and dropped out of the scan
entirely. Nothing surfaced it: a coin with no candles is indistinguishable from a
coin with no setup.

These tests pin the pacing itself rather than the throughput, because the real rate
limit lives on Hyperliquid's side and cannot be asserted here. What CAN be asserted
is that the gap is enforced, that it is configurable, and that disabling it is
genuinely free — a sleep that fires when the interval is 0 would tax every chart
request in the web container for nothing.
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.market_data import client


class PacingTests(SimpleTestCase):
    def setUp(self):
        client._last_call_at = 0.0

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0)
    def test_disabled_never_sleeps(self):
        with mock.patch.object(client.time, "sleep") as sleep:
            client._pace()
            client._pace()
        sleep.assert_not_called()

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0.2)
    def test_first_call_after_idle_does_not_sleep(self):
        # A long-idle process should not pay a penalty for its first request.
        with mock.patch.object(client.time, "monotonic", return_value=1000.0), \
                mock.patch.object(client.time, "sleep") as sleep:
            client._pace()
        sleep.assert_not_called()

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0.2)
    def test_back_to_back_calls_are_spaced(self):
        # Two calls at the same instant: the second waits the full interval.
        with mock.patch.object(client.time, "monotonic", return_value=1000.0), \
                mock.patch.object(client.time, "sleep") as sleep:
            client._pace()
            client._pace()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args[0][0], 0.2, places=6)

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0.2)
    def test_a_slow_caller_pays_nothing(self):
        # Enough time already passed doing real work — no artificial delay on top.
        times = iter([1000.0, 1000.0, 1000.5, 1000.5])
        with mock.patch.object(client.time, "monotonic", side_effect=lambda: next(times)), \
                mock.patch.object(client.time, "sleep") as sleep:
            client._pace()
            client._pace()
        sleep.assert_not_called()

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0.2)
    def test_partial_wait_is_only_the_remainder(self):
        times = iter([1000.0, 1000.0, 1000.15, 1000.2])
        with mock.patch.object(client.time, "monotonic", side_effect=lambda: next(times)), \
                mock.patch.object(client.time, "sleep") as sleep:
            client._pace()
            client._pace()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args[0][0], 0.05, places=6)

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0.2)
    def test_pacing_runs_on_the_request_path(self):
        # The gap is worthless if the request path skips it, so assert the wiring.
        resp = mock.Mock(status_code=200)
        with mock.patch.object(client.requests, "post", return_value=resp), \
                mock.patch.object(client, "_pace") as pace:
            client._post_info({"type": "meta"})
        pace.assert_called_once()

    @override_settings(HYPERLIQUID_MIN_REQUEST_INTERVAL=0.2)
    def test_each_retry_is_paced_too(self):
        # A retried call must not jump the queue — that is the burst this prevents.
        limited = mock.Mock(status_code=429, headers={})
        ok = mock.Mock(status_code=200)
        with mock.patch.object(client.requests, "post", side_effect=[limited, ok]), \
                mock.patch.object(client.time, "sleep"), \
                mock.patch.object(client, "_pace") as pace:
            client._post_info({"type": "meta"})
        self.assertEqual(pace.call_count, 2)
