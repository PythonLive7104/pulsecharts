from django.urls import path

from .views import (
    CustomStrategyDeleteView,
    CustomStrategyPreviewView,
    SignalAccuracyView,
    SignalFeedView,
    SignalServiceListView,
    SubscriptionDeleteView,
    SubscriptionListCreateView,
)

urlpatterns = [
    path("signal-services/", SignalServiceListView.as_view(), name="signal-services"),
    path("signal-services/accuracy/", SignalAccuracyView.as_view(), name="signal-accuracy"),
    path("signal-services/preview/", CustomStrategyPreviewView.as_view(), name="signal-service-preview"),
    path("signal-services/<int:pk>/", CustomStrategyDeleteView.as_view(), name="signal-service-detail"),
    path("me/signal-subscriptions/", SubscriptionListCreateView.as_view(), name="signal-subscriptions"),
    path("me/signal-subscriptions/<int:pk>/", SubscriptionDeleteView.as_view(), name="signal-subscription-detail"),
    path("me/signals/feed/", SignalFeedView.as_view(), name="signal-feed"),
]
