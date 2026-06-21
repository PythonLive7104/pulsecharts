"""Dodo Payments integration helpers (Section 3, 9, 15).

Kept thin and provider-isolated. Two responsibilities:
  1. create_checkout_session() — start a subscription checkout.
  2. verify_webhook_signature() — authenticate incoming webhook calls.

NOTE (Section 16): MAILIONDEV's Dodo merchant onboarding (TIN, corporate bank
account) may not be finalized. BILLING_LIVE gates real charges — keep it false
until payouts are confirmed, and gate the premium tier behind "coming soon".

The exact Dodo request/response shapes and signature scheme must be confirmed
against Dodo's current API docs before going live — the calls below are
structured but marked TODO where the contract needs verifying.
"""

import hashlib
import hmac

from django.conf import settings

# Flip to True only once Dodo onboarding + payouts are confirmed (Section 16).
BILLING_LIVE = bool(settings.DODO_PAYMENTS_API_KEY)


class DodoError(Exception):
    pass


# Map our plan keys to Dodo price IDs (fill in once products exist in Dodo).
PLAN_PRICE_IDS = {
    "starter": settings.DODO_PRICE_STARTER,
    "pro": settings.DODO_PRICE_PRO,
}


def create_checkout_session(*, user, plan: str, success_url: str, cancel_url: str) -> dict:
    """Create a Dodo checkout session for `plan` and return {checkout_url, session_id}.

    TODO: confirm endpoint, auth header, and field names against Dodo's API docs
    before charging real money. Reuse the InvoiceParsed merchant integration
    pattern (Section 2) once payouts are sorted.
    """
    if not BILLING_LIVE:
        raise DodoError("Billing is not live yet (Dodo onboarding pending).")

    price_id = PLAN_PRICE_IDS.get(plan)
    if not price_id:
        raise DodoError(f"No Dodo price configured for the '{plan}' plan.")

    # import requests
    # resp = requests.post(
    #     "https://api.dodopayments.com/v1/checkout/sessions",
    #     headers={"Authorization": f"Bearer {settings.DODO_PAYMENTS_API_KEY}"},
    #     json={
    #         "customer_email": user.email,
    #         "price_id": price_id,
    #         "success_url": success_url,
    #         "cancel_url": cancel_url,
    #         "metadata": {"user_id": user.id, "plan": plan},
    #     },
    #     timeout=15,
    # )
    # resp.raise_for_status()
    # data = resp.json()
    # return {"checkout_url": data["url"], "session_id": data["id"]}
    raise NotImplementedError("Wire up Dodo checkout once the contract is confirmed.")


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Constant-time HMAC check of the raw request body.

    TODO: confirm Dodo's exact signature scheme (header name, hashing, whether
    a timestamp is prefixed). This assumes HMAC-SHA256 over the raw body.
    """
    secret = settings.DODO_PAYMENTS_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
