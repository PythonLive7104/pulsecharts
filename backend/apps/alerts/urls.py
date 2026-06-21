from django.urls import path

from .views import (
    AlertDeleteView,
    AlertListCreateView,
    AlertSeenView,
    AlertUnseenCountView,
)

urlpatterns = [
    path("me/alerts/", AlertListCreateView.as_view(), name="alerts"),
    path("me/alerts/seen/", AlertSeenView.as_view(), name="alerts-seen"),
    path("me/alerts/unseen/", AlertUnseenCountView.as_view(), name="alerts-unseen"),
    path("me/alerts/<int:pk>/", AlertDeleteView.as_view(), name="alert-detail"),
]
