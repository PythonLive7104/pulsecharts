"""Plan tiers and their feature matrix (Section 11, 12).

Single source of truth for what each plan unlocks. Three tiers:

  - free     — live charts + a small taste of the signal feed (1 strategy)
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
        "strategies": 1,            # strategies a user can follow
        "signal_daily_quota": 5,    # signals/day in the feed (-1 = unlimited)
        "watchlist_limit": 20,
        "layout_limit": 1,
        "default_watchlist": 20,    # symbols pre-loaded at signup (onboarding)
        "default_strategies": 1,    # strategies followed by default
        "indicator_tiers": [FREE],
        "features": [
            "Live candlestick charts, all timeframes",
            "SMA, EMA & Volume overlays",
            "Starter watchlist of 20 coins, ready to go",
            "1 signal strategy followed for you",
            "Up to 5 signals/day",
        ],
    },
    STARTER: {
        "key": STARTER,
        "label": "Starter",
        "price_usd": 9,
        "period": "mo",
        "tagline": "Core indicators and a real signal feed.",
        "strategies": 4,
        "signal_daily_quota": 30,
        "watchlist_limit": 40,
        "layout_limit": 10,
        "default_watchlist": 40,    # symbols pre-loaded at signup (onboarding)
        "default_strategies": 4,    # strategies followed by default
        "indicator_tiers": [FREE, STARTER],
        "features": [
            "Everything in Free",
            "RSI, MACD, Bollinger Bands & VWAP",
            "Watchlist of 40 coins, set up for you",
            "4 signal strategies followed by default",
            "Up to 30 signals/day",
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
        "strategies": 10,
        "signal_daily_quota": -1,
        "watchlist_limit": 150,
        "layout_limit": 50,
        "default_watchlist": 150,   # symbols pre-loaded at signup (onboarding)
        "default_strategies": -1,   # follow every active strategy
        "indicator_tiers": [FREE, STARTER, PRO],
        "features": [
            "Everything in Starter",
            "Stochastic, ATR, Fibonacci & Ichimoku Cloud",
            "Watchlist of 150 coins, set up for you",
            "Every signal strategy followed by default",
            "Unlimited daily signals",
            "Telegram signal alerts",
            "Save up to 50 chart layouts",
        ],
    },
}

# Legacy tier value (before the 3-tier split) maps to Pro.
_ALIASES = {"premium": PRO}

PAID_TIERS = {STARTER, PRO}


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
