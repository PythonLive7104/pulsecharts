"""Paystack integration helpers.

Kept thin and provider-isolated (mirrors the old dodo.py). Three responsibilities:
  1. create_checkout_session() — start a one-time payment (Transaction Initialize,
     POST /transaction/initialize → hosted authorization_url).
  2. verify_webhook_signature() — authenticate incoming webhooks (HMAC-SHA512 of the
     raw body with the secret key, compared to the x-paystack-signature header).
  3. verify_transaction() — re-confirm a payment server-side (GET /transaction/verify)
     before granting access, so we never grant off a spoofed/replayed event.

Billing model: ONE-TIME payments that grant 30 days of access (no recurring
auto-charge, no Paystack Plans). Amounts are the USD plan prices in cents.

Mode (test vs live) is driven by PAYSTACK_MODE, which selects which key pair
settings exposes as PAYSTACK_SECRET_KEY / PAYSTACK_PUBLIC_KEY. Test keys never
move real money — use them to validate the flow end-to-end before going live.
"""

import hashlib
import hmac

import requests
from django.conf import settings

from apps.accounts.plans import purchase_price_usd

_BASE_URL = "https://api.paystack.co"

# Billing is "configured" once a secret key is present (test or live).
BILLING_LIVE = bool(settings.PAYSTACK_SECRET_KEY)


class PaystackError(Exception):
    pass


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def plan_amount_cents(plan: str) -> int:
    """The price of a purchasable option ('starter' | 'pro' | 'lifetime') as an
    integer number of cents (Paystack's subunit for USD). Derived from the single
    source of truth for prices (apps.accounts.plans)."""
    price = purchase_price_usd(plan)
    if not price:
        raise PaystackError(f"No price configured for the '{plan}' plan.")
    return int(round(float(price) * 100))


def create_checkout_session(*, user, plan: str, success_url: str, cancel_url: str) -> dict:
    """Initialize a Paystack transaction for `plan`; return {checkout_url, reference}.

    Sends our user_id + plan in metadata so the webhook can link the payment back to
    the account, and points callback_url at our success page. `cancel_url` is unused
    by Paystack's redirect flow (kept for signature parity with the old provider).
    """
    if not BILLING_LIVE:
        raise PaystackError("Billing is not configured (no Paystack secret key).")

    amount = plan_amount_cents(plan)
    try:
        resp = requests.post(
            f"{_BASE_URL}/transaction/initialize",
            headers=_headers(),
            json={
                "email": user.email,
                "amount": amount,
                "currency": settings.PAYSTACK_CURRENCY,
                "callback_url": success_url,
                "metadata": {
                    "user_id": str(user.id),
                    "plan": plan,
                    "cancel_action": cancel_url,
                },
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PaystackError(f"Could not reach Paystack: {exc}") from exc

    if resp.status_code >= 400:
        raise PaystackError(f"Paystack init failed ({resp.status_code}): {resp.text[:300]}")

    payload = resp.json()
    if not payload.get("status"):
        raise PaystackError(f"Paystack init rejected: {payload.get('message', 'unknown error')}")
    data = payload.get("data") or {}
    url = data.get("authorization_url")
    if not url:
        raise PaystackError("Paystack did not return an authorization_url.")
    return {"checkout_url": url, "reference": data.get("reference", "")}


def verify_webhook_signature(payload: bytes, headers) -> bool:
    """Verify a Paystack webhook: HMAC-SHA512 of the raw body with the secret key,
    hex-encoded, constant-time compared to the x-paystack-signature header."""
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret:
        return False
    sent = headers.get("x-paystack-signature", "")
    if not sent:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    return hmac.compare_digest(sent, expected)


def verify_transaction(reference: str) -> dict:
    """Fetch the authoritative transaction record from Paystack (GET
    /transaction/verify/{reference}). Returns the `data` object. Raises on any
    transport/API error so the caller can refuse to grant access."""
    if not reference:
        raise PaystackError("Missing transaction reference.")
    try:
        resp = requests.get(
            f"{_BASE_URL}/transaction/verify/{reference}",
            headers=_headers(),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PaystackError(f"Could not verify transaction: {exc}") from exc
    if resp.status_code >= 400:
        raise PaystackError(f"Paystack verify failed ({resp.status_code}): {resp.text[:300]}")
    payload = resp.json()
    if not payload.get("status"):
        raise PaystackError(f"Paystack verify rejected: {payload.get('message', 'unknown')}")
    return payload.get("data") or {}
