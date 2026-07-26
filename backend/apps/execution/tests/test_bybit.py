"""BybitClient margin-mode handling, with the HTTP layer mocked.

The subtle case: /v5/account/set-margin-mode can REFUSE the switch while still
returning retCode 0, explaining itself in a `reasons` array. Treating that as success
would let a trade go out believing losses are capped when the account is still cross.
"""

from unittest import mock

from django.test import TestCase

from apps.execution.bybit import BybitClient, BybitError


class MarginModeTests(TestCase):
    def setUp(self):
        self.client = BybitClient(api_key="k", api_secret="s", testnet=True)

    def test_get_margin_mode_reads_account_info(self):
        with mock.patch.object(self.client, "_request",
                               return_value={"marginMode": "REGULAR_MARGIN"}) as req:
            self.assertEqual(self.client.get_margin_mode(), "REGULAR_MARGIN")
        args = req.call_args[0]
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "/v5/account/info")

    def test_get_margin_mode_missing_field_is_empty_string(self):
        with mock.patch.object(self.client, "_request", return_value={}):
            self.assertEqual(self.client.get_margin_mode(), "")

    def test_set_margin_mode_posts_requested_mode(self):
        with mock.patch.object(self.client, "_request",
                               return_value={"reasons": []}) as req:
            self.client.set_margin_mode(BybitClient.ISOLATED)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "/v5/account/set-margin-mode")
        self.assertEqual(args[2], {"setMarginMode": "ISOLATED_MARGIN"})
        self.assertTrue(kwargs["signed"])

    def test_set_margin_mode_raises_when_bybit_reports_reasons(self):
        # retCode 0 but a non-empty `reasons` array = refused, not applied.
        with mock.patch.object(self.client, "_request", return_value={
            "reasons": [{"reasonCode": "3400045", "reasonMsg": "open positions exist"}]
        }):
            with self.assertRaises(BybitError) as ctx:
                self.client.set_margin_mode(BybitClient.ISOLATED)
        self.assertIn("open positions exist", str(ctx.exception))

    def test_set_margin_mode_reason_without_message_still_raises(self):
        with mock.patch.object(self.client, "_request",
                               return_value={"reasons": [{"reasonCode": "999"}]}):
            with self.assertRaises(BybitError):
                self.client.set_margin_mode(BybitClient.ISOLATED)

    def test_set_margin_mode_tolerates_missing_reasons_key(self):
        # A bare success payload must not be read as a refusal.
        with mock.patch.object(self.client, "_request", return_value={}):
            self.client.set_margin_mode(BybitClient.ISOLATED)  # no raise
