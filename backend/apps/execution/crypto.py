"""Symmetric encryption for broker API secrets at rest.

A user's Bybit API key + secret can move real money, so they are NEVER stored in
plaintext. They're encrypted with Fernet (AES-128-CBC + HMAC) under a single
app-wide key in ``settings.BROKER_ENCRYPTION_KEY`` — generate it once with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

and keep it only in the untracked ``.env``. Losing/rotating the key makes existing
stored credentials undecryptable (the user just re-enters their keys), so treat it
like any other production secret.

Deliberately fail-LOUD: if auto-trade is on but the key is missing/invalid, we raise
rather than silently storing recoverable secrets or trading blind. Callers in the
place path already run inside a try/except that records the failure per user.
"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings


class BrokerCryptoError(RuntimeError):
    """Raised when the encryption key is missing or malformed."""


@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet  # local import: optional dep

    key = getattr(settings, "BROKER_ENCRYPTION_KEY", "") or ""
    if not key:
        raise BrokerCryptoError(
            "BROKER_ENCRYPTION_KEY is not set — cannot store or read broker "
            "credentials. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add it to .env."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise BrokerCryptoError(f"BROKER_ENCRYPTION_KEY is invalid: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a secret to a storable token. Empty input stores as empty (a cleared
    credential), never an encrypted empty string."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a stored token back to plaintext. Empty token → empty string."""
    if not token:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        # Key rotated or data corrupted — the credential is unusable. Surface it so
        # the caller records an error and the user is prompted to re-enter keys,
        # rather than trading with a half-decrypted secret.
        raise BrokerCryptoError(
            "stored broker credential could not be decrypted (key rotated?)"
        ) from exc


def is_configured() -> bool:
    """True if a usable encryption key is present — cheap guard for callers that
    want to skip the broker path entirely when the app isn't set up."""
    try:
        _fernet()
        return True
    except BrokerCryptoError:
        return False
