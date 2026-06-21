from rest_framework import generics
from rest_framework.exceptions import ValidationError

from .models import WatchlistItem, watchlist_limit_for
from .serializers import WatchlistItemSerializer


class WatchlistView(generics.ListCreateAPIView):
    """GET/POST /api/watchlist/ (Section 9)."""

    serializer_class = WatchlistItemSerializer

    def get_queryset(self):
        return WatchlistItem.objects.filter(user=self.request.user).select_related(
            "symbol"
        )

    def perform_create(self, serializer):
        user = self.request.user
        limit = watchlist_limit_for(user)
        if WatchlistItem.objects.filter(user=user).count() >= limit:
            raise ValidationError(
                f"Watchlist limit reached ({limit}). Upgrade for more."
            )
        # Unique (user, symbol) is enforced at the DB level too.
        symbol = serializer.validated_data["symbol"]
        if WatchlistItem.objects.filter(user=user, symbol=symbol).exists():
            raise ValidationError("Symbol already in watchlist.")
        serializer.save(user=user)


class WatchlistItemView(generics.DestroyAPIView):
    """DELETE /api/watchlist/{id}/ (Section 9)."""

    serializer_class = WatchlistItemSerializer

    def get_queryset(self):
        return WatchlistItem.objects.filter(user=self.request.user)
