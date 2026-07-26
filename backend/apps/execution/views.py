"""Auto-trade API (v2, apps.execution).

GET    /api/me/broker/            current broker connection status + risk envelope
PUT    /api/me/broker/            save/update keys, risk envelope, and enabled flag
DELETE /api/me/broker/            disconnect (delete the credential + keys)
POST   /api/me/broker/test/       verify the stored keys against Bybit (wallet read)
GET    /api/me/trades/            this user's auto-trade execution history

All endpoints require auth (DRF default) and are premium-gated where it matters —
connecting is allowed for anyone, but the executor only acts for premium users.
"""

from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import crypto
from .bybit import BybitClient, BybitError
from .models import BrokerCredential, TradeExecution
from .permissions import IsPaidUser
from .serializers import BrokerCredentialSerializer, TradeExecutionSerializer


class BrokerCredentialView(APIView):
    """Get / upsert / delete the caller's single broker credential."""

    permission_classes = [IsPaidUser]

    def _get(self, user):
        return BrokerCredential.objects.filter(user=user).first()

    def get(self, request):
        cred = self._get(request.user)
        if not cred:
            return Response({"connected": False, "has_keys": False, "enabled": False})
        data = BrokerCredentialSerializer(cred).data
        data["connected"] = cred.has_keys
        return Response(data)

    def put(self, request):
        cred = self._get(request.user)
        serializer = BrokerCredentialSerializer(
            cred, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        try:
            cred = serializer.save()
        except crypto.BrokerCryptoError as exc:
            # Encryption key missing/invalid — a server config problem, surfaced
            # clearly rather than storing an unusable secret.
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        out = BrokerCredentialSerializer(cred).data
        out["connected"] = cred.has_keys
        return Response(out)

    def delete(self, request):
        cred = self._get(request.user)
        if cred:
            cred.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BrokerTestView(APIView):
    """Verify the stored keys can read the account (a real Bybit round-trip)."""

    permission_classes = [IsPaidUser]

    def post(self, request):
        cred = BrokerCredential.objects.filter(user=request.user).first()
        if not cred or not cred.has_keys:
            return Response(
                {"ok": False, "detail": "No API keys saved."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            client = BybitClient(cred.api_key, cred.api_secret, testnet=cred.testnet)
            equity = client.get_wallet_equity_usdt()
            # Surfaced so the user can SEE whether losses are capped per-trade, rather
            # than discovering the account was on cross margin after a bad fill.
            margin_mode = client.get_margin_mode()
        except crypto.BrokerCryptoError as exc:
            return Response({"ok": False, "detail": str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except BybitError as exc:
            return Response({"ok": False, "detail": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "ok": True, "testnet": cred.testnet, "usdt_equity": equity,
            "margin_mode": margin_mode,
            "isolated": margin_mode == BybitClient.ISOLATED,
        })


class TradeExecutionListView(generics.ListAPIView):
    serializer_class = TradeExecutionSerializer
    permission_classes = [IsPaidUser]

    def get_queryset(self):
        return TradeExecution.objects.filter(user=self.request.user)
