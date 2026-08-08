"""Placeholder substitution + the unsubscribe token.

Deliberately NOT Django's template engine: campaign HTML is pasted in by an admin,
and rendering it as a template would let a stray `{%` break the send, or worse expose
template internals. Plain string replacement on a fixed placeholder list can only ever
produce the text it was given.
"""

from __future__ import annotations

from django.conf import settings
from django.core import signing

# Substitutions available in campaign HTML. Anything else is left untouched.
PLACEHOLDERS = (
    "{{email}}", "{{name}}", "{{lifetime_url}}", "{{billing_url}}",
    "{{unsubscribe_url}}", "{{price}}",
)

_UNSUB_SALT = "campaign-unsubscribe"


def unsubscribe_token(user) -> str:
    """Signed, non-expiring token. No login required to unsubscribe — demanding one
    is exactly the friction that gets mail marked as spam instead."""
    return signing.dumps({"uid": user.pk}, salt=_UNSUB_SALT)


def user_from_token(token: str):
    from apps.accounts.models import User

    try:
        data = signing.loads(token, salt=_UNSUB_SALT)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data.get("uid")).first()


def context_for(user) -> dict:
    from apps.accounts.plans import LIFETIME_PLAN

    base = settings.FRONTEND_URL.rstrip("/")
    name = (user.first_name or "").strip() or user.email.split("@")[0]
    return {
        "{{email}}": user.email,
        "{{name}}": name,
        # ?plan=lifetime preselects the lifetime option on the billing page, so the
        # click lands on the thing the email advertised rather than a pricing table.
        "{{lifetime_url}}": f"{base}/account/billing?plan=lifetime",
        "{{billing_url}}": f"{base}/account/billing",
        "{{unsubscribe_url}}": f"{base}/unsubscribe/{unsubscribe_token(user)}",
        "{{price}}": f"${LIFETIME_PLAN['price_usd']}",
    }


def render_html(html: str, user) -> str:
    out = html
    for key, value in context_for(user).items():
        out = out.replace(key, value)
    return out


def preview_html(html: str, user=None) -> str:
    """Render with a real user when given one, otherwise with obvious sample values —
    so a preview never silently shows an empty {{name}} and looks fine."""
    if user is not None:
        return render_html(html, user)
    base = settings.FRONTEND_URL.rstrip("/")
    from apps.accounts.plans import LIFETIME_PLAN

    sample = {
        "{{email}}": "sample.user@example.com",
        "{{name}}": "Sample",
        "{{lifetime_url}}": f"{base}/account/billing?plan=lifetime",
        "{{billing_url}}": f"{base}/account/billing",
        "{{unsubscribe_url}}": f"{base}/unsubscribe/SAMPLE-TOKEN",
        "{{price}}": f"${LIFETIME_PLAN['price_usd']}",
    }
    out = html
    for key, value in sample.items():
        out = out.replace(key, value)
    return out
