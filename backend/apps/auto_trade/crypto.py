"""Symmetric encryption for broker API credentials at rest.

Broker API keys/secrets are sensitive: even scoped to trade-only with no
withdrawal, a leak lets an attacker move a user's funds around. So we never store
them in plaintext — they're Fernet-encrypted with `BROKER_ENCRYPTION_KEY` and
only decrypted in-memory at the moment we sign an exchange request.

Rotating BROKER_ENCRYPTION_KEY invalidates every stored credential (decryption
will raise), which forces a reconnect — acceptable, and the safe failure mode.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class BrokerCryptoError(Exception):
    """Raised when a credential can't be encrypted/decrypted."""


def _fernet():
    key = settings.BROKER_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured(
            "BROKER_ENCRYPTION_KEY is not set — cannot store broker credentials. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(f"BROKER_ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a credential for storage. Returns a base64 token (str)."""
    if plaintext is None:
        raise BrokerCryptoError("cannot encrypt None")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a stored credential back to plaintext."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        # Wrong/rotated key, or corrupted ciphertext.
        raise BrokerCryptoError("could not decrypt broker credential (key rotated?)") from exc
