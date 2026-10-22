"""Telegram delivery stops chasing chats that are permanently gone.

A blocked bot never unblocks itself. Production was retrying thirteen such chats on
every push cycle — twice a minute, indefinitely — which wasted requests and buried
genuine failures under a wall of identical warnings. A permanent error now switches
delivery off for that chat.

The distinction that matters is permanent vs transient: a 500 or a timeout MUST stay
retryable, because deactivating a user over a blip would silently stop their signals
with nothing in the UI to explain it.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts import telegram

User = get_user_model()


def api(ok, description=None):
    """A fake Bot API response body."""
    return {"ok": ok} if ok else {"ok": False, "description": description}


@override_settings(TELEGRAM_BOT_TOKEN="test-token")
class PermanentErrorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="blocked@example.com", password="x",
            telegram_chat_id="555", telegram_active=True,
        )

    def send(self, body):
        with mock.patch.object(telegram, "_request", return_value=body):
            return telegram.send_message("555", "hi")

    def test_blocked_user_is_deactivated(self):
        self.assertFalse(self.send(api(False, "Forbidden: bot was blocked by the user")))
        self.user.refresh_from_db()
        self.assertFalse(self.user.telegram_active)

    def test_deactivated_account_is_deactivated(self):
        self.send(api(False, "Forbidden: user is deactivated"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.telegram_active)

    def test_missing_chat_is_deactivated(self):
        self.send(api(False, "Bad Request: chat not found"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.telegram_active)

    def test_chat_id_is_kept_so_the_user_can_reconnect(self):
        # Clearing the chat id would force a full relink; telegram_can_reconnect
        # exists precisely for this state.
        self.send(api(False, "Forbidden: bot was blocked by the user"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.telegram_chat_id, "555")
        self.assertTrue(self.user.telegram_can_reconnect)
        self.assertFalse(self.user.telegram_connected)

    def test_transient_failure_does_NOT_deactivate(self):
        self.assertFalse(self.send(api(False, "Internal Server Error")))
        self.user.refresh_from_db()
        self.assertTrue(self.user.telegram_active, "a blip must not stop delivery")

    def test_network_failure_does_NOT_deactivate(self):
        with mock.patch.object(telegram, "_request", return_value=None):
            self.assertFalse(telegram.send_message("555", "hi"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.telegram_active)

    def test_success_leaves_the_user_alone(self):
        self.assertTrue(self.send(api(True)))
        self.user.refresh_from_db()
        self.assertTrue(self.user.telegram_active)

    def test_other_users_on_other_chats_are_untouched(self):
        other = User.objects.create_user(
            email="fine@example.com", password="x",
            telegram_chat_id="999", telegram_active=True,
        )
        self.send(api(False, "Forbidden: bot was blocked by the user"))
        other.refresh_from_db()
        self.assertTrue(other.telegram_active)


class ClassifierTests(TestCase):
    def test_permanent_descriptions(self):
        for text in ("Forbidden: bot was blocked by the user",
                     "Forbidden: user is deactivated",
                     "Bad Request: chat not found"):
            self.assertTrue(telegram._is_permanent(text), text)

    def test_transient_descriptions(self):
        for text in ("Too Many Requests: retry after 5", "Internal Server Error",
                     "Bad Gateway", None, ""):
            self.assertFalse(telegram._is_permanent(text), text)
