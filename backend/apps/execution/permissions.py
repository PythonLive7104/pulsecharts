"""Auto-trade is a paid feature — Starter or Pro only.

Enforced server-side on every broker endpoint (not just in the UI / executor) so a
Free user can't connect a broker or flip auto-trade on by calling the API directly.
"""

from rest_framework.permissions import BasePermission

from apps.accounts.plans import is_paid


class IsPaidUser(BasePermission):
    """Allow only authenticated users on an active Starter or Pro plan (expiry-aware
    via is_paid). Free / lapsed users get 403."""

    message = "Auto-trade is available on the Starter and Pro plans."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and is_paid(user))
