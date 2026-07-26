"""Tests for broker-secret encryption at rest."""

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from apps.execution import crypto

_KEY = Fernet.generate_key().decode()


def _reset_cache():
    crypto._fernet.cache_clear()  # the key is memoized; drop it between settings


@override_settings(BROKER_ENCRYPTION_KEY=_KEY)
class CryptoRoundTripTests(SimpleTestCase):
    def setUp(self):
        _reset_cache()

    def tearDown(self):
        _reset_cache()

    def test_roundtrip(self):
        token = crypto.encrypt("my-secret-key")
        self.assertNotEqual(token, "my-secret-key")
        self.assertEqual(crypto.decrypt(token), "my-secret-key")

    def test_empty_stays_empty(self):
        self.assertEqual(crypto.encrypt(""), "")
        self.assertEqual(crypto.decrypt(""), "")

    def test_ciphertext_is_not_deterministic(self):
        # Fernet embeds a random IV, so two encryptions of the same value differ —
        # a stored secret can't be matched by comparing ciphertexts.
        self.assertNotEqual(crypto.encrypt("abc"), crypto.encrypt("abc"))

    def test_is_configured_true_with_key(self):
        self.assertTrue(crypto.is_configured())


class CryptoMisconfiguredTests(SimpleTestCase):
    def setUp(self):
        _reset_cache()

    def tearDown(self):
        _reset_cache()

    @override_settings(BROKER_ENCRYPTION_KEY="")
    def test_missing_key_raises(self):
        with self.assertRaises(crypto.BrokerCryptoError):
            crypto.encrypt("x")
        self.assertFalse(crypto.is_configured())

    @override_settings(BROKER_ENCRYPTION_KEY="not-a-valid-fernet-key")
    def test_invalid_key_raises(self):
        with self.assertRaises(crypto.BrokerCryptoError):
            crypto.encrypt("x")
