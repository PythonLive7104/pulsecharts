from django.urls import path

from .views import BrokerCredentialView, BrokerTestView, TradeExecutionListView

urlpatterns = [
    path("me/broker/", BrokerCredentialView.as_view(), name="broker-credential"),
    path("me/broker/test/", BrokerTestView.as_view(), name="broker-test"),
    path("me/trades/", TradeExecutionListView.as_view(), name="trade-executions"),
]
