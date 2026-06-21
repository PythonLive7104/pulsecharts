"""WebSocket URL routing (Section 7, 9 — WS /ws/market/)."""

from django.urls import path

from apps.market_data.consumers import MarketConsumer

websocket_urlpatterns = [
    path("ws/market/", MarketConsumer.as_asgi()),
]
