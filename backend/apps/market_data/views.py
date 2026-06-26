import requests
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .feeds import get_candles, supported_intervals
from .models import Symbol
from .serializers import SymbolSerializer


class SymbolListView(generics.ListAPIView):
    """GET /api/symbols/ — available symbols (Section 9)."""

    queryset = Symbol.objects.filter(is_active=True)
    serializer_class = SymbolSerializer
    permission_classes = [permissions.AllowAny]


class CandlesView(APIView):
    """GET /api/symbols/{ticker}/candles/?interval=1m&limit=500 (Section 9).

    Historical OHLCV for the initial chart load, normalized (Section 6.2).
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request, ticker):
        try:
            symbol = Symbol.objects.get(ticker=ticker, is_active=True)
        except Symbol.DoesNotExist:
            return Response(
                {"detail": "Unknown symbol."}, status=status.HTTP_404_NOT_FOUND
            )

        # Plan gate: a Pro-only symbol (e.g. gold) returns no history to anyone
        # below the required plan, so the chart can't load for them.
        from apps.accounts.plans import plan_allows

        if not plan_allows(request.user, symbol.min_plan):
            return Response(
                {"detail": f"{symbol.ticker} is available on the {symbol.get_min_plan_display()} plan. Upgrade to chart it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        interval = request.query_params.get("interval", "1m")
        if interval not in supported_intervals(symbol):
            return Response(
                {"detail": f"Unsupported interval '{interval}' for this symbol."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = min(int(request.query_params.get("limit", 500)), 5000)
        except ValueError:
            limit = 500

        try:
            candles = get_candles(symbol, interval, limit)
        except requests.RequestException:
            return Response(
                {"detail": "Upstream market data unavailable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"symbol": ticker, "interval": interval, "candles": candles})
