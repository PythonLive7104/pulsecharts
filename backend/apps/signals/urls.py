from django.urls import path

from .views import (
    SignalAccuracyView,
    SignalFeedView,
    SignalServiceListView,
    SubscriptionDeleteView,
    SubscriptionListCreateView,
)

urlpatterns = [
    path("signal-services/", SignalServiceListView.as_view(), name="signal-services"),
    path("signal-services/accuracy/", SignalAccuracyView.as_view(), name="signal-accuracy"),
    path("me/signal-subscriptions/", SubscriptionListCreateView.as_view(), name="signal-subscriptions"),
    path("me/signal-subscriptions/<int:pk>/", SubscriptionDeleteView.as_view(), name="signal-subscription-detail"),
    path("me/signals/feed/", SignalFeedView.as_view(), name="signal-feed"),
]
