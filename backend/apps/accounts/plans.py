"""Plan tiers and their feature matrix (Section 11, 12).

Single source of truth for what each plan unlocks. Three tiers:

  - free     — live charts + a small taste of the signal feed (2 strategies)
  - starter  — core premium indicators + a real signal feed
  - pro      — everything, unlimited signals

Everything plan-gated (signal quota, strategies you can follow, indicators,
watchlist size, saved layouts) is derived from here so there's one place to tune
pricing/limits. -1 means "unlimited".

New users (and existing ones, via `manage.py provision_defaults`) are seeded with
a ready-made watchlist and a set of followed strategies sized by their plan —
`default_watchlist` / `default_strategies` below — so they land on a useful
dashboard instead of an empty one (apps.accounts.onboarding). `default_strategies`
of -1 means "follow every active strategy".
"""

from __future__ import annotations

from django.utils import timezone

# Tier keys.
FREE = "free"
STARTER = "starter"
PRO = "pro"

PLANS: dict[str, dict] = {
    FREE: {
        "key": FREE,
        "label": "Free",
        "price_usd": 0,
        "period": "",
        "tagline": "Live crypto charts and a taste of signals.",
        "strategies": 2,            # strategies a user can follow
        "signal_weekly_quota": 20,  # signals/week in the feed (-1 = unlimited)
        "watchlist_limit": 20,
        "layout_limit": 1,
        "default_watchlist": 20,    # symbols pre-loaded at signup (onboarding)
        "default_strategies": 2,    # strategies followed by default
        "custom_strategies_per_month": 0,  # Pro-only feature
        "indicator_tiers": [FREE],
        "features": [
            "Live candlestick charts, all timeframes",
            "SMA, EMA & Volume overlays",
            "Starter watchlist of 20 coins, ready to go",
            "2 signal strategies followed for you",
            "Up to 20 signals/week",
        ],
    },
    STARTER: {
        "key": STARTER,
        "label": "Starter",
        "price_usd": 9,
        "period": "mo",
        "tagline": "Core indicators and a real signal feed.",
        "strategies": 4,
        "signal_weekly_quota": 400,
        "watchlist_limit": 40,
        "layout_limit": 10,
        "default_watchlist": 40,    # symbols pre-loaded at signup (onboarding)
        "default_strategies": 4,    # strategies followed by default
        "custom_strategies_per_month": 0,  # Pro-only feature
        "indicator_tiers": [FREE, STARTER],
        "features": [
            "Everything in Free",
            "RSI, MACD, Bollinger Bands & VWAP",
            "Watchlist of 40 coins, set up for you",
            "4 signal strategies followed by default",
            "Up to 400 signals/week",
            "Telegram signal alerts",
            "Save up to 10 chart layouts",
        ],
    },
    PRO: {
        "key": PRO,
        "label": "Pro",
        "price_usd": 19,
        "period": "mo",
        "tagline": "Every indicator and strategy, unlimited signals.",
        # Follow cap = 8 active built-in strategies + 5 custom (Pro-only) a user can
        # build. Custom follows auto-subscribe and bypass this cap at creation but
        # still count toward it, so the cap must cover both or re-following a built-in
        # gets blocked once customs exist. Keep in sync with active built-ins +
        # custom_strategies_per_month.
        "strategies": 13,
        "signal_weekly_quota": -1,
        "watchlist_limit": 150,
        "layout_limit": 50,
        "default_watchlist": 150,   # symbols pre-loaded at signup (onboarding)
        "default_strategies": -1,   # follow every active strategy
        "custom_strategies_per_month": 5,  # create your own strategies (rolling 30d)
        "indicator_tiers": [FREE, STARTER, PRO],
        "features": [
            "Everything in Starter",
            "Build your own strategy with AI (up to 5/mo)",
            "Stochastic, ATR, Fibonacci & Ichimoku Cloud",
            "Watchlist of 150 coins, set up for you",
            "Every signal strategy followed by default",
            "Unlimited signals",
            "Telegram signal alerts",
            "Save up to 50 chart layouts",
        ],
    },
}

# Purchase option, NOT a tier — deliberately kept out of PLANS so plan_key /
# plan_rank / PAID_TIERS never have to know about it. Buying it grants the Pro
# tier with a null plan_expiry, which plan_key() below already reads as
# "never expires". Everything downstream (entitlements, gating, quotas) sees a
# normal Pro user.
LIFETIME = "lifetime"

LIFETIME_PLAN: dict = {
    "key": LIFETIME,
    "label": "Pro Lifetime",
    "price_usd": 89,
    "period": "once",
    "grants_tier": PRO,
    "tagline": "Every Pro feature, forever. One payment, no renewals.",
    "features": [
        "Everything in Pro, for life",
        "One payment — never expires, never renews",
        "Build your own strategy with AI (up to 5/mo)",
        "Every indicator: Stochastic, ATR, Fibonacci & Ichimoku Cloud",
        "Watchlist of 150 coins, set up for you",
        "Unlimited signals + Telegram alerts",
        "Save up to 50 chart layouts",
    ],
}

# Everything a user can pay for, keyed by what the checkout endpoint accepts.
PURCHASE_OPTIONS: dict[str, dict] = {
    STARTER: PLANS[STARTER],
    PRO: PLANS[PRO],
    LIFETIME: LIFETIME_PLAN,
}

# Legacy tier value (before the 3-tier split) maps to Pro.
_ALIASES = {"premium": PRO}

PAID_TIERS = {STARTER, PRO}

# Tiers from least to most privileged. Used to compare a user's plan against a
# minimum-plan requirement (e.g. a Pro-only symbol — apps.market_data).
PLAN_ORDER = [FREE, STARTER, PRO]


def plan_key(user) -> str:
    """Resolve a user's *effective* plan key, honoring expiry.

    An expired paid plan falls back to Free. Unknown/legacy tiers are normalized.
    """
    tier = getattr(user, "plan_tier", FREE) or FREE
    tier = _ALIASES.get(tier, tier)
    if tier not in PLANS:
        return FREE
    if tier in PAID_TIERS:
        expiry = getattr(user, "plan_expiry", None)
        if expiry is not None and expiry <= timezone.now():
            return FREE
    return tier


def plan_for(user) -> dict:
    """The full feature dict for a user's effective plan."""
    return PLANS[plan_key(user)]


def is_paid(user) -> bool:
    return plan_key(user) in PAID_TIERS


def has_perpetual_access(user) -> bool:
    """True for a user on a paid tier that never expires, however they got there —
    a lifetime purchase OR a staff grant (`set_plan --tier pro` with no --days).

    A null plan_expiry on a paid tier is exactly what plan_key() treats as
    non-expiring, so this is the same condition read from the other side. Guards
    every timed-grant path, since writing an expiry onto these users would silently
    downgrade them.
    """
    if plan_key(user) not in PAID_TIERS:
        return False
    return getattr(user, "plan_expiry", None) is None


def is_lifetime_purchaser(user) -> bool:
    """True only for users who actually BOUGHT the lifetime plan — perpetual access
    backed by a real payment record.

    Deliberately narrower than has_perpetual_access(): a staff-granted perpetual Pro
    has no lifetime Subscription row, so they still see pricing and can still buy.
    This is the flag the UI hides pricing on. A charged-back lifetime row flips to
    `disputed`, so it stops counting here too.
    """
    if not has_perpetual_access(user):
        return False
    from .models import Subscription  # local import: models imports this module

    return Subscription.objects.filter(
        user=user,
        renewal_date__isnull=True,  # what a lifetime grant writes
        status=Subscription.Status.ACTIVE,
    ).exists()


def purchase_price_usd(option: str) -> int | float | None:
    """USD price of a purchasable option ('starter' | 'pro' | 'lifetime')."""
    return PURCHASE_OPTIONS.get(option, {}).get("price_usd")


def tier_granted_by(option: str) -> str:
    """The plan tier a purchase grants. Lifetime grants Pro; the rest grant themselves."""
    return PURCHASE_OPTIONS.get(option, {}).get("grants_tier", option)


def plan_rank(key: str) -> int:
    """Position of a plan key in PLAN_ORDER (higher = more privileged).

    Unknown/blank keys rank as Free (0), so an unset minimum never gates anything.
    """
    try:
        return PLAN_ORDER.index(_ALIASES.get(key, key))
    except ValueError:
        return 0


def plan_allows(user, min_plan_key: str) -> bool:
    """True if the user's effective plan meets a minimum-plan requirement.

    Used to gate access to plan-restricted resources like Pro-only symbols.
    Anonymous users resolve to Free, so anything above Free is blocked for them.
    """
    return plan_rank(plan_key(user)) >= plan_rank(min_plan_key or FREE)
