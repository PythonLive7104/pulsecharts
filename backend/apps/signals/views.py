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
from apps.market_data.forex import market_open as forex_market_open
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
# How many live signal cards a feed page carries. A large watchlist across several
# strategies yields >100 concurrent cards; sending and rendering them in one go is what
# made the page take seconds to paint. The client pages through the rest.
FEED_PAGE_SIZE = 20
# Below this many resolved trades, the accuracy figure is reported as provisional
# rather than as a track record — a dozen trades is noise, not a win rate.
MIN_ACCURACY_SAMPLE = 20


def _next_market_open():
    """When the scan resumes: the next Sunday 21:00 UTC (see forex.market_open)."""
    now = timezone.now()
    days_ahead = (6 - now.weekday()) % 7  # 6 = Sunday
    resume = (now + timedelta(days=days_ahead)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    if resume <= now:  # already past Sunday 21:00 — next week's open
        resume += timedelta(days=7)
    return resume


def _followed_service_ids(user):
    """Service ids this user follows AND is allowed to see.

    Enforced at read time, not just at write time. A subscription row to someone else's
    custom strategy should never exist — but one did (onboarding auto-followed every
    active service, private strategies included), and because the feed trusted the
    subscription table blindly, another user's private signals were delivered straight
    into the victim's feed and Telegram. Re-check ownership here so a bad row can only
    ever be inert, never a leak.
    """
    return list(
        UserSignalSubscription.objects.filter(user=user)
        .filter(Q(service__owner__isnull=True) | Q(service__owner=user))
        .values_list("service_id", flat=True)
    )


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
        # Live follows only. A subscription row outlives the strategy being disabled
        # (nothing deletes follows when a strategy is switched off), so a user who
        # followed a strategy that was later retired kept counting it forever — the
        # dashboard showed "10 strategies followed" against a roster of 7. The rows
        # are deliberately KEPT, not deleted: re-enabling a strategy should restore
        # the follows it had.
        return UserSignalSubscription.objects.filter(
            user=self.request.user, service__is_active=True
        ).select_related("service")

    def create(self, request, *args, **kwargs):
        user = request.user
        # Each plan caps how many strategies you can follow (free = 2). Count only
        # follows of ACTIVE strategies — otherwise a dead follow silently eats a slot
        # and a Free user could be locked out of following anything real.
        allowed = strategies_allowed_for(user)
        following = UserSignalSubscription.objects.filter(
            user=user, service__is_active=True
        ).count()
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

        def _int_param(name, default, lo, hi):
            try:
                return max(lo, min(hi, int(request.query_params.get(name, default))))
            except (TypeError, ValueError):
                return default

        limit = _int_param("limit", FEED_PAGE_SIZE, 1, 100)
        offset = _int_param("offset", 0, 0, 10_000)

        # Weekend pause. Derived from the SAME window the scan uses (forex.market_open:
        # closed Fri 21:00 → Sun 21:00 UTC) and the SAME setting, so the banner can
        # never claim signals are paused when the engine is still generating them.
        # Forex always pauses (its market is shut); crypto only if the flag is set.
        market_open = forex_market_open()
        pause = {
            "paused": not market_open,
            "crypto_paused": not market_open and settings.SIGNAL_SKIP_CRYPTO_WEEKEND,
            "resumes_at": _next_market_open().isoformat(),
        }

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

        followed_ids = _followed_service_ids(user)

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

        # Page the active feed. A 150-symbol watchlist across 7 strategies produces well
        # over a hundred live cards; serializing and rendering them all made the page
        # take seconds to appear even though the query itself answers in ~60ms. The
        # dedup above must run over the FULL set first (a trade's newest row can sit
        # anywhere in the list), so the slice happens here, after it.
        active_total = len(active)
        active = active[offset:offset + limit]
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

        # Results history: resolved calls the user was ACTUALLY delivered — the same
        # SignalDelivery rows that back the Trade updates panel, the Telegram pushes
        # and the accuracy headline, so all four reconcile. It used to be the wider
        # followed-strategy × watchlist pool (a track-record teaser), which surfaced
        # closures for trades the user was never handed and read as their own. A
        # signal delivered under a strategy the user has SINCE unfollowed still
        # counts: it was given to them and it resolved, so it stays on their record.
        # Newest resolution first, capped by plan (RESULTS_LIMIT_*). One row per TRADE
        # (symbol, tf, direction, entry): pre-fix duplicate deliveries must not render
        # one trade as several rows, while a later DISTINCT trade on the same pair has
        # a different entry and stays separate. Over-fetch before the dedup so we
        # still fill the plan's limit.
        # Only page 0 carries the resolved history — a "load more" click is asking for
        # the next slice of live cards, not for the results list to be rebuilt.
        if offset:
            return Response(
                {
                    "quota": quota,
                    "delivered_this_week": delivered_this_week,
                    "signals": SignalSerializer(active, many=True).data,
                    "signals_total": active_total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + len(active) < active_total,
                    "pause": pause,
                    "resolved": [],
                    "disclaimer": "Informational only. Not financial advice.",
                }
            )

        results_limit = RESULTS_LIMIT_PAID if is_paid(user) else RESULTS_LIMIT_FREE
        delivered_ids = SignalDelivery.objects.filter(user=user).values_list(
            "signal_id", flat=True
        )
        resolved_pool = (
            Signal.objects.filter(
                id__in=delivered_ids,
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
                "signals_total": active_total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(active) < active_total,
                "pause": pause,
                "resolved": SignalSerializer(resolved, many=True).data,
                "disclaimer": "Informational only. Not financial advice.",
            }
        )


class SignalAccuracyView(APIView):
    """GET /api/signal-services/accuracy/ — realized win-rate stats (Section 18).

    YOUR track record: scoped to the signals this user was actually DELIVERED, so the
    headline describes the cards they were handed, not every call the strategies made
    on coins they happen to watch. (The wider pool still backs the "Past results"
    teaser list, which is explicitly a strategy history — but a percentage rendered as
    a headline reads as "how did MY signals do", so it must answer that question.)

    Staff can pass ?scope=all for the product-wide figure across every user's signals.
    That is a wider, more honest sample for tuning — it is NOT a way to keep a poor
    number away from users. Section 18 commits to reporting realized accuracy honestly
    even when it is unflattering, and users trading real money off these cards are
    exactly who the number is for.
    """

    def get(self, request):
        user = request.user

        if request.query_params.get("scope") == "all" and user.is_staff:
            base = Signal.objects.filter(
                service__owner__isnull=True,
                direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
                resolved_at__isnull=False,
            )
            return Response({**accuracy_stats(base), "scope": "all"})

        # The exact signals delivered to this user (same rows the Trade updates panel
        # and the Telegram pushes are built from), bounded to the results window.
        # resolved_at__gte both bounds the window and excludes still-open calls.
        delivered_ids = SignalDelivery.objects.filter(user=user).values_list(
            "signal_id", flat=True
        )
        # Closed trades PLUS still-running trades that have already banked a target. A
        # TP1-tagged runner has a third secured at 1R with the stop at breakeven, so it
        # can no longer become a loss — its win/loss classification is settled even
        # though its final R isn't. It's counted at its locked floor (stats.
        # _effective_outcome), never at its potential.
        #
        # This is only honest if the OTHER open trades are disclosed too. Open calls
        # that haven't tagged TP1 are genuinely undecided — some are walking into a
        # stop — and silently dropping them while counting the tagged ones would
        # cherry-pick the winners out of the open pile and inflate the win rate. They
        # can't be counted (nobody knows how they end), so they must at least be
        # SHOWN: `undecided` below, surfaced next to the headline.
        base = Signal.objects.filter(
            Q(resolved_at__gte=timezone.now() - RESULTS_LOOKBACK)
            | Q(outcome=Signal.Outcome.PENDING, best_tp__gte=1),
            id__in=delivered_ids,
            direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
        )
        stats = accuracy_stats(base)

        def _trades(qs):
            return qs.values(
                "symbol_id", "timeframe", "direction", "entry_price"
            ).distinct().count()

        open_qs = Signal.objects.filter(
            id__in=delivered_ids,
            direction__in=[Signal.Direction.BUY, Signal.Direction.SELL],
            outcome=Signal.Outcome.PENDING,
        )
        # Open, no target tagged yet: outcome genuinely unknown. NOT in the win rate.
        stats["undecided"] = _trades(open_qs.filter(best_tp=0))
        # The closed-only record, always shown alongside: the headline leans on open
        # positions, and the reader is entitled to see the settled number too.
        stats["closed_only"] = accuracy_stats(base.exclude(outcome=Signal.Outcome.PENDING))["overall"]
        # A handful of trades is not a track record. Flag small samples so the UI can
        # present the figure as provisional rather than as a confident headline —
        # under-reporting the sample size is how misleading accuracy claims get made
        # (Section 13.7), in either direction. Judged on CLOSED trades: a headline
        # resting mostly on open positions isn't settled however large it gets.
        stats["provisional"] = (stats["closed_only"]["resolved"] or 0) < MIN_ACCURACY_SAMPLE
        stats["min_sample"] = MIN_ACCURACY_SAMPLE
        stats["scope"] = "delivered"
        return Response(stats)
