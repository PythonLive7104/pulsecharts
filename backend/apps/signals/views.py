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
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.plans import PRO, is_paid, plan_key
from apps.watchlists.models import WatchlistItem, watchlist_limit_for

from . import confluence
from .engine import SignalEngineError
from .models import (
    Signal,
    SignalDelivery,
    SignalService,
    StrategyCreationLog,
    UserSignalSubscription,
)
from .quota import (
    SIGNAL_QUOTA_WINDOW,
    custom_strategy_quota_for,
    signal_quota_for,
    strategies_allowed_for,
)
from .serializers import (
    SignalSerializer,
    SignalServiceSerializer,
    SubscriptionSerializer,
)
from .stats import accuracy_stats
from .strategy_builder import StrategyBuildError, build_rule_from_text

# How far back the feed will consider undelivered signals.
FEED_LOOKBACK = timedelta(days=2)
# How far back the resolved-results history reaches.
RESULTS_LOOKBACK = timedelta(days=7)
# How many resolved trades the "Past results" panel shows. Free gets a smaller
# teaser (enough social proof to convert, not the whole track record); paid tiers
# see the full history. This is closed/historical outcomes only — the live,
# actionable feed stays strictly capped at the plan's weekly quota.
RESULTS_LIMIT_FREE = 10
RESULTS_LIMIT_PAID = 50


def _visible_services(user):
    """Active built-in strategies plus this user's own custom strategies. Other
    users' custom strategies are never listed."""
    qs = SignalService.objects.filter(is_active=True)
    if user and user.is_authenticated:
        return qs.filter(Q(owner__isnull=True) | Q(owner=user))
    return qs.filter(owner__isnull=True)


def _unique_custom_slug(user, name: str) -> str:
    import secrets

    base = f"u{user.id}-{slugify(name) or 'strategy'}"[:70]
    slug = base
    while SignalService.objects.filter(slug=slug).exists():
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug[:80]


class SignalServiceListView(APIView):
    """GET  /api/signal-services/  → visible strategies + the user's custom-strategy quota.
    POST /api/signal-services/  → create a custom strategy from a plain-English sentence
    (Pro-only, rolling-30-day creation cap)."""

    def get(self, request):
        services = _visible_services(request.user).order_by("owner_id", "name")
        data = SignalServiceSerializer(services, many=True, context={"request": request}).data
        return Response({
            "services": data,
            "custom_quota": custom_strategy_quota_for(request.user),
        })

    def post(self, request):
        user = request.user
        if plan_key(user) != PRO:
            return Response(
                {"detail": "Creating your own strategies is a Pro feature. Upgrade to build one."},
                status=status.HTTP_403_FORBIDDEN,
            )
        quota = custom_strategy_quota_for(user)
        if quota["remaining"] <= 0:
            return Response(
                {"detail": (
                    f"You've used all {quota['limit']} custom strategies for this period. "
                    "Deleting one doesn't free a slot — a slot opens 30 days after each creation."
                ), "custom_quota": quota},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            built = build_rule_from_text(request.data.get("text", ""))
        except StrategyBuildError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except SignalEngineError:
            return Response(
                {"detail": "The strategy builder is unavailable right now. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        name = (request.data.get("name") or built["name"]).strip()[:80] or "Custom strategy"
        service = SignalService.objects.create(
            owner=user,
            name=name,
            slug=_unique_custom_slug(user, name),
            description=built["description"],
            strategy_type="custom",
            rule_config=built["rule_config"],
            is_active=True,
        )
        StrategyCreationLog.objects.create(user=user)  # append-only: never refunded
        # Auto-follow so signals start flowing immediately (bypasses the follow cap —
        # it's the user's own strategy).
        UserSignalSubscription.objects.get_or_create(user=user, service=service)
        return Response(
            {
                "service": SignalServiceSerializer(service, context={"request": request}).data,
                "summary": built["summary"],
                "custom_quota": custom_strategy_quota_for(user),
            },
            status=status.HTTP_201_CREATED,
        )


class CustomStrategyPreviewView(APIView):
    """POST /api/signal-services/preview/ → interpret a sentence into a rule WITHOUT
    saving or counting quota, so the user can confirm before creating."""

    def post(self, request):
        if plan_key(request.user) != PRO:
            return Response(
                {"detail": "Creating your own strategies is a Pro feature."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            built = build_rule_from_text(request.data.get("text", ""))
        except StrategyBuildError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except SignalEngineError:
            return Response(
                {"detail": "The strategy builder is unavailable right now. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({
            "name": built["name"],
            "description": built["description"],
            "summary": built["summary"],
            "rule_config": built["rule_config"],
        })


class CustomStrategyDeleteView(generics.DestroyAPIView):
    """DELETE /api/signal-services/{id}/ → delete OWN custom strategy (cascades its
    signals + subscription). Does not refund the creation quota."""

    def get_queryset(self):
        return SignalService.objects.filter(owner=self.request.user)


class SubscriptionListCreateView(generics.ListCreateAPIView):
    serializer_class = SubscriptionSerializer

    def get_queryset(self):
        return UserSignalSubscription.objects.filter(
            user=self.request.user
        ).select_related("service")

    def create(self, request, *args, **kwargs):
        user = request.user
        # Each plan caps how many strategies you can follow (free = 2).
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
        # Can't follow someone else's custom strategy (built-in = owner None, or your own).
        svc = serializer.validated_data["service"]
        if svc.owner_id is not None and svc.owner_id != user.id:
            return Response({"detail": "Strategy not found."}, status=status.HTTP_404_NOT_FOUND)
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

    New signals from followed strategies are delivered up to the plan's weekly
    quota (rolling 7-day window); delivery is recorded so the same signal isn't
    shown twice and the quota can't be bypassed by the client.
    """

    def get(self, request):
        user = request.user
        quota = signal_quota_for(user)
        now = timezone.now()
        week_cutoff = now - SIGNAL_QUOTA_WINDOW  # rolling 7-day quota window

        # A plan with no signal access (quota 0) gets a locked upgrade card. No
        # current plan is 0 — Free gets a real 20/week feed — so this is a guard for
        # any future no-access tier, not the Free path.
        if quota == 0:
            return Response({
                "locked": True,
                "quota": 0,
                "delivered_this_week": 0,
                "signals": [],
                "disclaimer": "Trading signals are a Premium feature.",
            })

        # Shadow mode (Section 13.7): generate + evaluate but don't surface.
        if settings.SIGNAL_SHADOW_MODE:
            return Response({
                "shadow": True,
                "quota": quota,
                "delivered_this_week": 0,
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
                "delivered_this_week": 0,
                "signals": [],
                "disclaimer": "Add coins to your watchlist to receive signals for them.",
            })

        followed_ids = list(
            UserSignalSubscription.objects.filter(user=user).values_list("service_id", flat=True)
        )

        delivered_this_week = SignalDelivery.objects.filter(
            user=user, delivered_at__gte=week_cutoff
        ).count()
        unlimited = quota < 0
        remaining = None if unlimited else max(0, quota - delivered_this_week)

        # Deliver new qualifying signals up to the remaining quota. Collapse by
        # confluence first, so a coin firing on several strategies costs one
        # delivery (one card) rather than flooding the quota with near-duplicates.
        if followed_ids and (unlimited or remaining > 0):
            # Dedup deliveries at the TRADE grain (symbol, timeframe, direction,
            # entry_price), NOT per signal_id. The scan stores one signal per strategy
            # and collapse picks a representative, so as strategies joined a setup a
            # new rep was delivered each time — duplicate cards + wasted quota for the
            # SAME trade. Keyed on what was delivered in the lookback window (not on
            # the rep still being PENDING — a fast rep that resolved would otherwise
            # let a sibling re-deliver the same trade). A genuinely new trade has a
            # different entry, so it still comes through.
            delivered_trades = set(
                SignalDelivery.objects.filter(
                    user=user, delivered_at__gte=now - FEED_LOOKBACK,
                ).values_list(
                    "signal__symbol_id", "signal__timeframe", "signal__direction", "signal__entry_price",
                )
            )
            candidates = list(
                Signal.objects.filter(
                    confluence.deliverable_q(),  # custom strategies bypass the conf floor
                    service_id__in=followed_ids,
                    symbol_id__in=watched_ids,
                    direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                    outcome=Signal.Outcome.PENDING,
                    generated_at__gte=now - FEED_LOOKBACK,
                )
                .select_related("service")
                .order_by("-generated_at")
            )
            reps = [
                r for r in confluence.collapse(candidates)  # one per symbol+tf, newest first
                if (r.symbol_id, r.timeframe, r.direction, r.entry_price) not in delivered_trades
            ]
            if not unlimited:
                reps = reps[:remaining]
            SignalDelivery.objects.bulk_create(
                [SignalDelivery(user=user, signal=s) for s in reps],
                ignore_conflicts=True,
            )

        delivered_this_week = SignalDelivery.objects.filter(
            user=user, delivered_at__gte=week_cutoff
        ).count()

        # Active feed: signals delivered this week that are still live (PENDING).
        week_ids = SignalDelivery.objects.filter(
            user=user, delivered_at__gte=week_cutoff
        ).values_list("signal_id", flat=True)
        active = list(
            Signal.objects.filter(
                id__in=week_ids,
                service_id__in=followed_ids,  # only strategies you currently follow
                symbol_id__in=watched_ids,
                outcome=Signal.Outcome.PENDING,
            )
            .select_related("symbol", "service")
            .order_by("-generated_at")
        )
        # One card per TRADE (symbol, timeframe, direction, entry_price): pre-fix
        # duplicate deliveries (and any future edge) must not render the same trade as
        # multiple cards, while two genuinely distinct trades on the same pair stay
        # separate. Keeps the newest signal per trade; agreement count is filled by
        # annotate() below from the full sibling pool.
        seen, deduped = set(), []
        for s in active:
            key = (s.symbol_id, s.timeframe, s.direction, s.entry_price)
            if key not in seen:
                seen.add(key)
                deduped.append(s)
        active = deduped
        # Annotate each shown signal with how many followed strategies currently
        # agree on it (the sibling pool within the feed lookback), for the card's
        # "N strategies agree" badge.
        if active:
            pool = Signal.objects.filter(
                confluence.deliverable_q(),  # custom strategies bypass the conf floor
                service_id__in=followed_ids,
                symbol_id__in=watched_ids,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                generated_at__gte=now - FEED_LOOKBACK,
            ).select_related("service")
            confluence.annotate(active, pool)

        # Results history: resolved calls the user was ACTUALLY delivered, so they
        # can see which of their past signals worked out — win or loss. Scoped to
        # this user's SignalDelivery rows (not just followed strategy + watchlist):
        # its win/loss track record is the social proof that drives upgrades, so an
        # empty panel hurts conversion. Instead of hiding undelivered results, cap
        # the COUNT by plan: Free sees a small teaser, paid tiers the full history
        # (RESULTS_LIMIT_*). These are closed/historical trades — the live,
        # actionable feed above is what stays strictly quota-capped, so this teaser
        # doesn't hand over the paid product. Newest resolution first. One row per
        # TRADE, not per strategy: the scan stores one signal per strategy, so a
        # single trade resolved as N identical rows (e.g. XAU 1h SELL "stopped out"
        # ×6). Dedup on (symbol, tf, direction, entry) — strategies firing the same
        # setup share the entry/stop, while a later DISTINCT trade on the same pair
        # has a different entry, so it stays a separate row. (The per-strategy win
        # rates in SignalAccuracyView are unaffected — different endpoint.) Over-fetch
        # before the dedup so we still fill the plan's limit.
        results_limit = RESULTS_LIMIT_PAID if is_paid(user) else RESULTS_LIMIT_FREE
        resolved_pool = (
            Signal.objects.filter(
                confluence.deliverable_q(),  # custom strategies bypass the conf floor
                service_id__in=followed_ids,
                symbol_id__in=watched_ids,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                resolved_at__gte=now - RESULTS_LOOKBACK,
            )
            .exclude(outcome=Signal.Outcome.PENDING)
            .select_related("symbol", "service")
            .order_by("-resolved_at")[:250]
        )
        seen_trades, resolved = set(), []
        for s in resolved_pool:
            key = (s.symbol_id, s.timeframe, s.direction, s.entry_price)
            if key in seen_trades:
                continue
            seen_trades.add(key)
            resolved.append(s)
            if len(resolved) >= results_limit:
                break

        return Response(
            {
                "quota": quota,
                "delivered_this_week": delivered_this_week,
                "signals": SignalSerializer(active, many=True).data,
                "resolved": SignalSerializer(resolved, many=True).data,
                "disclaimer": "Informational only. Not financial advice.",
            }
        )


class SignalAccuracyView(APIView):
    """GET /api/signal-services/accuracy/ — realized win-rate stats (Section 18).

    Scoped to the SAME filter as the "Past results" panel (SignalFeedView): the
    caller's followed strategies, watched symbols, and the results lookback window.
    This is deliberate — the two views sit side by side, so a resolved trade that
    counts toward the accuracy number is exactly one the user can also see in their
    results list. Following only your own custom strategies means this scope also
    keeps another user's private strategy out of your stats.
    """

    def get(self, request):
        user = request.user
        followed_ids = list(
            UserSignalSubscription.objects.filter(user=user).values_list("service_id", flat=True)
        )
        watched_ids = list(
            WatchlistItem.objects.filter(user=user).values_list("symbol_id", flat=True)
        )
        # Same predicate as SignalFeedView's resolved_pool. resolved_at__gte both
        # bounds the window and excludes still-open (null resolved_at) calls.
        base = Signal.objects.filter(
            confluence.deliverable_q(),
            service_id__in=followed_ids,
            symbol_id__in=watched_ids,
            direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
            resolved_at__gte=timezone.now() - RESULTS_LOOKBACK,
        )
        return Response(accuracy_stats(base))
