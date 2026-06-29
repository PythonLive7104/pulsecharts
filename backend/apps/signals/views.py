"""Signals API (Section 13.5).

GET    /api/signal-services/                 list strategies (+ is_followed)
GET    /api/me/signal-subscriptions/         user's followed strategies
POST   /api/me/signal-subscriptions/         follow a strategy
DELETE /api/me/signal-subscriptions/{id}/    unfollow
GET    /api/me/signals/feed/                 personalized feed, capped by quota
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.watchlists.models import WatchlistItem, watchlist_limit_for

from . import confluence
from .models import Signal, SignalDelivery, SignalService, UserSignalSubscription
from .quota import signal_quota_for, strategies_allowed_for
from .serializers import (
    SignalSerializer,
    SignalServiceSerializer,
    SubscriptionSerializer,
)
from .stats import accuracy_stats

# How far back the feed will consider undelivered signals.
FEED_LOOKBACK = timedelta(days=2)
# How far back the resolved-results history reaches.
RESULTS_LOOKBACK = timedelta(days=7)


class SignalServiceListView(generics.ListAPIView):
    queryset = SignalService.objects.filter(is_active=True)
    serializer_class = SignalServiceSerializer


class SubscriptionListCreateView(generics.ListCreateAPIView):
    serializer_class = SubscriptionSerializer

    def get_queryset(self):
        return UserSignalSubscription.objects.filter(
            user=self.request.user
        ).select_related("service")

    def create(self, request, *args, **kwargs):
        user = request.user
        # Each plan caps how many strategies you can follow (free = 1).
        allowed = strategies_allowed_for(user)
        following = UserSignalSubscription.objects.filter(user=user).count()
        if allowed == 0:
            return Response(
                {"detail": "Trading signals aren't available on your plan. Upgrade to follow strategies."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if following >= allowed:
            return Response(
                {"detail": (
                    f"Your plan lets you follow {allowed} "
                    f"{'strategy' if allowed == 1 else 'strategies'}. "
                    "Upgrade to follow more, or unfollow one first."
                )},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save(user=request.user)
        except IntegrityError:
            return Response(
                {"detail": "Already following this strategy."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SubscriptionDeleteView(generics.DestroyAPIView):
    def get_queryset(self):
        return UserSignalSubscription.objects.filter(user=self.request.user)


class SignalFeedView(APIView):
    """Personalized feed, quota-enforced server-side (Section 13.3).

    New signals from followed strategies are delivered up to the plan's daily
    quota; delivery is recorded so the same signal isn't shown twice and the
    quota can't be bypassed by the client.
    """

    def get(self, request):
        user = request.user
        quota = signal_quota_for(user)
        now = timezone.now()
        today = now.date()

        # Premium-only: free users (quota 0) get a locked feed.
        if quota == 0:
            return Response({
                "locked": True,
                "quota": 0,
                "delivered_today": 0,
                "signals": [],
                "disclaimer": "Trading signals are a Premium feature.",
            })

        # Shadow mode (Section 13.7): generate + evaluate but don't surface.
        if settings.SIGNAL_SHADOW_MODE:
            return Response({
                "shadow": True,
                "quota": quota,
                "delivered_today": 0,
                "signals": [],
                "disclaimer": "Signals are in validation. Check back soon.",
            })

        # Signals are scoped to the coins the user watches. Empty watchlist →
        # nothing to show; prompt them to add symbols (capped by their plan).
        watched_ids = list(
            WatchlistItem.objects.filter(user=user).values_list("symbol_id", flat=True)
        )
        if not watched_ids:
            return Response({
                "needs_watchlist": True,
                "watchlist_limit": watchlist_limit_for(user),
                "quota": quota,
                "delivered_today": 0,
                "signals": [],
                "disclaimer": "Add coins to your watchlist to receive signals for them.",
            })

        followed_ids = list(
            UserSignalSubscription.objects.filter(user=user).values_list("service_id", flat=True)
        )

        delivered_today = SignalDelivery.objects.filter(
            user=user, delivered_at__date=today
        ).count()
        unlimited = quota < 0
        remaining = None if unlimited else max(0, quota - delivered_today)

        # Deliver new qualifying signals up to the remaining quota. Collapse by
        # confluence first, so a coin firing on several strategies costs one
        # delivery (one card) rather than flooding the quota with near-duplicates.
        if followed_ids and (unlimited or remaining > 0):
            already = SignalDelivery.objects.filter(user=user).values_list("signal_id", flat=True)
            candidates = list(
                Signal.objects.filter(
                    service_id__in=followed_ids,
                    symbol_id__in=watched_ids,
                    direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                    confidence_pct__gte=settings.SIGNAL_MIN_CONFIDENCE,
                    generated_at__gte=now - FEED_LOOKBACK,
                )
                .exclude(id__in=already)
                .select_related("service")
                .order_by("-generated_at")
            )
            reps = confluence.collapse(candidates)  # one per symbol+tf, newest first
            if not unlimited:
                reps = reps[:remaining]
            SignalDelivery.objects.bulk_create(
                [SignalDelivery(user=user, signal=s) for s in reps],
                ignore_conflicts=True,
            )

        delivered_today = SignalDelivery.objects.filter(
            user=user, delivered_at__date=today
        ).count()

        # Active feed: signals delivered today that are still live (PENDING).
        today_ids = SignalDelivery.objects.filter(
            user=user, delivered_at__date=today
        ).values_list("signal_id", flat=True)
        active = list(
            Signal.objects.filter(
                id__in=today_ids,
                service_id__in=followed_ids,  # only strategies you currently follow
                symbol_id__in=watched_ids,
                outcome=Signal.Outcome.PENDING,
            )
            .select_related("symbol", "service")
            .order_by("-generated_at")
        )
        # Annotate each shown signal with how many followed strategies currently
        # agree on it (the sibling pool within the feed lookback), for the card's
        # "N strategies agree" badge.
        if active:
            pool = Signal.objects.filter(
                service_id__in=followed_ids,
                symbol_id__in=watched_ids,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                confidence_pct__gte=settings.SIGNAL_MIN_CONFIDENCE,
                generated_at__gte=now - FEED_LOOKBACK,
            ).select_related("service")
            confluence.annotate(active, pool)

        # Results history: resolved calls from the strategies this user follows,
        # so they can see which past signals worked out — win or loss. Based on
        # followed strategies (not lazy delivery), so a fast call that hit its TP
        # before the feed was opened still shows up. Newest resolution first.
        resolved = (
            Signal.objects.filter(
                service_id__in=followed_ids,
                symbol_id__in=watched_ids,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                confidence_pct__gte=settings.SIGNAL_MIN_CONFIDENCE,
                resolved_at__gte=now - RESULTS_LOOKBACK,
            )
            .exclude(outcome=Signal.Outcome.PENDING)
            .select_related("symbol", "service")
            .order_by("-resolved_at")[:50]
        )

        return Response(
            {
                "quota": quota,
                "delivered_today": delivered_today,
                "signals": SignalSerializer(active, many=True).data,
                "resolved": SignalSerializer(resolved, many=True).data,
                "disclaimer": "Informational only. Not financial advice.",
            }
        )


class SignalAccuracyView(APIView):
    """GET /api/signal-services/accuracy/ — realized win-rate stats (Section 18)."""

    def get(self, request):
        return Response(accuracy_stats())
