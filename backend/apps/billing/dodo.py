"""Dodo Payments integration helpers (Section 3, 9, 15).

Kept thin and provider-isolated. Two responsibilities:
  1. create_checkout_session() — start a subscription checkout (Checkout Sessions
     API, POST /checkouts → hosted checkout_url).
  2. verify_webhook_signature() — authenticate incoming webhooks using the
     Standard Webhooks spec that Dodo implements.

Mode (test vs live) is driven by DODO_PAYMENTS_MODE. Test mode hits
test.dodopayments.com and never moves real money — use it to validate the flow
end-to-end before flipping to live.
"""

import base64
import binascii
import hashlib
import hmac
import time

import requests
from django.conf import settings

# Dodo API base URLs by mode.
_BASE_URLS = {
    "test": "https://test.dodopayments.com",
    "live": "https://live.dodopayments.com",
}

# Tolerated clock skew for webhook timestamps (replay protection), in seconds.
_WEBHOOK_TOLERANCE = 300

# Billing is "configured" once an API key is present. Real charges only happen
# in live mode (DODO_PAYMENTS_MODE=live).
BILLING_LIVE = bool(settings.DODO_PAYMENTS_API_KEY)


class DodoError(Exception):
    pass


def _base_url() -> str:
    mode = (settings.DODO_PAYMENTS_MODE or "test").lower()
    return _BASE_URLS.get(mode, _BASE_URLS["test"])


def _plan_product_ids() -> dict:
    """Our plan keys → Dodo product IDs (only non-empty ones)."""
    return {
        k: v
        for k, v in {
            "starter": settings.DODO_PRICE_STARTER,
            "pro": settings.DODO_PRICE_PRO,
        }.items()
        if v
    }


def product_tier_map() -> dict:
    """Dodo product ID → our plan key (webhook fallback when metadata is absent)."""
    return {v: k for k, v in _plan_product_ids().items()}


def create_checkout_session(*, user, plan: str, success_url: str, cancel_url: str) -> dict:
    """Create a Dodo Checkout Session for `plan`; return {checkout_url, session_id}.

    Sends our user_id + plan in metadata so the webhook can link the resulting
    subscription back to the account that paid.
    """
    if not BILLING_LIVE:
        raise DodoError("Billing is not configured (no Dodo API key).")

    product_id = _plan_product_ids().get(plan)
    if not product_id:
        raise DodoError(f"No Dodo product configured for the '{plan}' plan.")

    try:
        resp = requests.post(
            f"{_base_url()}/checkouts",
            headers={
                "Authorization": f"Bearer {settings.DODO_PAYMENTS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "product_cart": [{"product_id": product_id, "quantity": 1}],
                "customer": {"email": user.email},
                "return_url": success_url,
                "metadata": {"user_id": str(user.id), "plan": plan},
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise DodoError(f"Could not reach Dodo: {exc}") from exc

    if resp.status_code >= 400:
        raise DodoError(f"Dodo checkout failed ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    url = data.get("checkout_url")
    if not url:
        raise DodoError("Dodo did not return a checkout_url.")
    return {"checkout_url": url, "session_id": data.get("session_id", "")}


def verify_webhook_signature(payload: bytes, headers) -> bool:
    """Verify a Dodo webhook using the Standard Webhooks scheme.

    Signed content is "{webhook-id}.{webhook-timestamp}.{raw-body}", HMAC-SHA256
    with the base64-decoded secret (after the "whsec_" prefix), base64-encoded.
    The webhook-signature header is a space-separated list of "v1,<sig>" entries.
    """
    secret = settings.DODO_PAYMENTS_WEBHOOK_SECRET
    if not secret:
        return False

    webhook_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    sig_header = headers.get("webhook-signature", "")
    if not (webhook_id and timestamp and sig_header):
        return False

    # Replay protection: reject timestamps outside the tolerance window.
    try:
        if abs(time.time() - int(timestamp)) > _WEBHOOK_TOLERANCE:
            return False
    except ValueError:
        return False

    key = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        key_bytes = base64.b64decode(key)
    except (ValueError, binascii.Error):
        return False

    signed = b".".join([webhook_id.encode(), timestamp.encode(), payload])
    expected = base64.b64encode(
        hmac.new(key_bytes, signed, hashlib.sha256).digest()
    ).decode()

    for entry in sig_header.split():
        _, _, sig = entry.partition(",")  # strip the "v1," version prefix
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False
