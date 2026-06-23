"""Auto-trade API (Pro-tier).

  GET    /api/me/broker/              connection status
  POST   /api/me/broker/              connect/replace credentials (verifies on Bybit)
  DELETE /api/me/broker/              disconnect + purge credentials
  GET    /api/me/auto-trade/config/   risk-envelope config
  PUT    /api/me/auto-trade/config/   update config
  GET    /api/me/auto-trade/executions/   recent execution history
  POST   /api/me/auto-trade/panic/    kill switch — disable auto-trade now

Everything here is gated to the Pro plan and only does anything while the global
settings.AUTO_TRADE_ENABLED flag is on. Connecting/configuring is allowed even
when the global flag is off (so users can set up ahead of launch), but no order
is ever placed until it's flipped on.
"""

import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from .brokers import BrokerError, get_client
from .models import AutoTradeConfig, BrokerConnection, TradeExecution
from .serializers import (
    AutoTradeConfigSerializer,
    BrokerConnectionSerializer,
    BrokerConnectSerializer,
    TradeExecutionSerializer,
)

logger = logging.getLogger("auto_trade.views")


class IsProUser(permissions.BasePermission):
    """Auto-trade is a Pro-only feature (expiry-aware)."""

    message = "Auto-trade is available on the Pro plan."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.plan_key == "pro")


class BrokerConnectionView(APIView):
    permission_classes = [IsProUser]

    def get(self, request):
        conn = getattr(request.user, "broker_connection", None)
        if conn is None:
            return Response({"connected": False})
        data = BrokerConnectionSerializer(conn).data
        data["connected"] = True
        return Response(data)

    def post(self, request):
        s = BrokerConnectSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        # Build a transient connection to verify the key BEFORE persisting it —
        # we never store a credential we haven't confirmed is trade-only.
        conn = getattr(request.user, "broker_connection", None) or BrokerConnection(
            user=request.user
        )
        conn.broker = BrokerConnection.Broker.BYBIT
        conn.testnet = data["testnet"]
        conn.set_credentials(data["api_key"], data["api_secret"])

        try:
            result = get_client(conn).verify()
        except BrokerError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        conn.last_checked_at = timezone.now()
        if not result.safe:
            conn.status = BrokerConnection.Status.ERROR
            conn.permission_verified = False
            conn.last_error = result.message or "verification failed"
            conn.save()
            return Response({"detail": conn.last_error}, status=status.HTTP_400_BAD_REQUEST)

        conn.status = BrokerConnection.Status.ACTIVE
        conn.permission_verified = True
        conn.last_error = ""
        conn.save()
        out = BrokerConnectionSerializer(conn).data
        out["connected"] = True
        return Response(out, status=status.HTTP_201_CREATED)

    def delete(self, request):
        conn = getattr(request.user, "broker_connection", None)
        if conn is not None:
            conn.delete()  # purges encrypted credentials
        return Response(status=status.HTTP_204_NO_CONTENT)


def _default_config_fields():
    """Seed a new config from the server-side defaults."""
    return {
        "risk_pct": settings.AUTO_TRADE_DEFAULT_RISK_PCT,
        "leverage": settings.AUTO_TRADE_DEFAULT_LEVERAGE,
        "max_open_positions": settings.AUTO_TRADE_MAX_OPEN_POSITIONS,
        "max_daily_trades": settings.AUTO_TRADE_MAX_DAILY_TRADES,
        "max_slippage_pct": settings.AUTO_TRADE_MAX_SLIPPAGE_PCT,
        "max_signal_age_sec": settings.AUTO_TRADE_MAX_SIGNAL_AGE_SEC,
        "tp_distribution": [25, 25, 25, 25],
    }


class AutoTradeConfigView(APIView):
    permission_classes = [IsProUser]

    def get(self, request):
        cfg, _ = AutoTradeConfig.objects.get_or_create(
            user=request.user, defaults=_default_config_fields()
        )
        return Response(AutoTradeConfigSerializer(cfg).data)

    def put(self, request):
        cfg, _ = AutoTradeConfig.objects.get_or_create(
            user=request.user, defaults=_default_config_fields()
        )
        s = AutoTradeConfigSerializer(cfg, data=request.data, partial=True)
        s.is_valid(raise_exception=True)

        # Don't let a user enable auto-trade without a verified, usable broker.
        if s.validated_data.get("enabled"):
            conn = getattr(request.user, "broker_connection", None)
            if conn is None or not conn.is_usable:
                return Response(
                    {"detail": "Connect and verify a broker before enabling auto-trade."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        s.save()
        return Response(AutoTradeConfigSerializer(cfg).data)


class TradeExecutionListView(ListAPIView):
    permission_classes = [IsProUser]
    serializer_class = TradeExecutionSerializer

    def get_queryset(self):
        return (
            TradeExecution.objects.filter(user=self.request.user)
            .select_related("signal", "signal__symbol", "signal__service")
        )


class AutoTradePanicView(APIView):
    """Kill switch: immediately disable auto-trade AND flatten open positions.

    Disabling the config (no new trades) always happens, even if the broker call
    fails — that part can't depend on the exchange being reachable. Then, when the
    feature is live, it market-closes every open position and cancels the leftover
    protective orders. Runs synchronously so the response reflects the real result.
    """

    permission_classes = [IsProUser]

    def post(self, request):
        cfg = getattr(request.user, "auto_trade_config", None)
        if cfg is not None and cfg.enabled:
            cfg.enabled = False
            cfg.save(update_fields=["enabled", "updated_at"])

        flat = {"closed": 0}
        if settings.AUTO_TRADE_ENABLED:
            from .tasks import flatten_open_trades

            try:
                flat = flatten_open_trades(request.user)
            except Exception:
                logger.exception("panic flatten failed for user %s", request.user.id)
                flat = {"closed": 0, "error": "could not flatten positions — check the exchange"}

        detail = "Auto-trade disabled."
        if flat.get("closed"):
            detail += f" Closed {flat['closed']} open position(s)."
        if flat.get("error"):
            detail += f" Warning: {flat['error']}."
        if flat.get("errors"):
            detail += (f" Warning: {flat['errors']} position(s) could not be closed — "
                       "check the exchange.")
        return Response({"enabled": False, "closed": flat.get("closed", 0), "detail": detail})
