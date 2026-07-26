"""API gating tests — the broker endpoints are Starter/Pro only."""

from datetime import timedelta

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.execution import crypto
from apps.execution.models import BrokerCredential

_KEY = Fernet.generate_key().decode()
User = get_user_model()


@override_settings(BROKER_ENCRYPTION_KEY=_KEY)
class BrokerApiGatingTests(APITestCase):
    def setUp(self):
        crypto._fernet.cache_clear()

    def tearDown(self):
        crypto._fernet.cache_clear()

    def _user(self, tier="free", days=30):
        expiry = timezone.now() + timedelta(days=days) if tier != "free" else None
        return User.objects.create_user(f"{tier}@ex.com", "pw", plan_tier=tier, plan_expiry=expiry)

    def test_free_user_blocked_from_broker_endpoints(self):
        self.client.force_authenticate(self._user("free"))
        self.assertEqual(self.client.get("/api/me/broker/").status_code, 403)
        self.assertEqual(
            self.client.put("/api/me/broker/", {"testnet": True}, format="json").status_code, 403
        )
        self.assertEqual(self.client.get("/api/me/trades/").status_code, 403)
        self.assertEqual(self.client.post("/api/me/broker/test/").status_code, 403)

    def test_expired_paid_user_blocked(self):
        self.client.force_authenticate(self._user("starter", days=-1))  # lapsed
        self.assertEqual(self.client.get("/api/me/broker/").status_code, 403)

    def test_starter_user_allowed(self):
        self.client.force_authenticate(self._user("starter"))
        res = self.client.get("/api/me/broker/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["connected"])

    def test_pro_user_can_save_keys(self):
        self.client.force_authenticate(self._user("pro"))
        res = self.client.put(
            "/api/me/broker/",
            {"api_key": "k", "api_secret": "s", "testnet": True}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["connected"])
        # Secret is never echoed back.
        self.assertNotIn("api_secret", res.data)

    @override_settings(AUTO_TRADE_DEFAULT_MARGIN_USD=2.5)
    def test_new_credential_uses_env_default_margin(self):
        user = self._user("pro")
        self.client.force_authenticate(user)
        self.client.put(
            "/api/me/broker/",
            {"api_key": "k", "api_secret": "s"}, format="json",
        )
        cred = BrokerCredential.objects.get(user=user)
        self.assertEqual(cred.margin_per_trade_usd, 2.5)

    def test_cannot_enable_without_keys(self):
        self.client.force_authenticate(self._user("pro"))
        res = self.client.put("/api/me/broker/", {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 400)
