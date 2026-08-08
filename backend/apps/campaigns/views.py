"""Unsubscribe endpoint. Public and login-free by design.

Requiring a login to stop marketing email is the single most effective way to get
reported as spam instead of unsubscribed. The token is signed, so the link can only
opt out the account it was minted for.
"""

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .render import user_from_token


class UnsubscribeView(APIView):
    """POST /api/campaigns/unsubscribe/<token>/ — opt out of marketing email."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, token):
        user = user_from_token(token)
        if user is None:
            return Response({"detail": "That unsubscribe link isn't valid."}, status=400)
        if not user.marketing_opt_out:
            user.marketing_opt_out = True
            user.save(update_fields=["marketing_opt_out"])
        return Response({"detail": "You've been unsubscribed from marketing emails.",
                         "email": user.email})

    # Some clients (and link scanners) fetch with GET; treat it the same so the link
    # works wherever it's opened.
    def get(self, request, token):
        return self.post(request, token)
