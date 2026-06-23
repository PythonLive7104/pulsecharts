from django.urls import path

from .views import (
    AutoTradeConfigView,
    AutoTradePanicView,
    BrokerConnectionView,
    TradeExecutionListView,
)

urlpatterns = [
    path("me/broker/", BrokerConnectionView.as_view(), name="broker-connection"),
    path("me/auto-trade/config/", AutoTradeConfigView.as_view(), name="auto-trade-config"),
    path("me/auto-trade/executions/", TradeExecutionListView.as_view(), name="auto-trade-executions"),
    path("me/auto-trade/panic/", AutoTradePanicView.as_view(), name="auto-trade-panic"),
]
