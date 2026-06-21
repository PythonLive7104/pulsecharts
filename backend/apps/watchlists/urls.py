from django.urls import path

from .views import WatchlistItemView, WatchlistView

urlpatterns = [
    path("watchlist/", WatchlistView.as_view(), name="watchlist"),
    path("watchlist/<int:pk>/", WatchlistItemView.as_view(), name="watchlist-item"),
]
