"""Indicator catalog and tier mapping (Section 5, 10, 11, 12).

The indicators themselves are computed client-side (Section 10) — this catalog
only exists so the entitlements endpoint can tell the frontend which options to
show unlocked vs. locked. Gating is UI-level by design (Section 10/11): these are
all derived from public OHLCV data and cannot be technically protected, so we
keep this catalog simple rather than over-investing in obfuscation.
"""

# slug -> human label, grouped by the plan tier that unlocks them.
FREE_INDICATORS = {
    "sma": "Simple Moving Average",
    "ema": "Exponential Moving Average",
    "volume": "Volume",
}

# Core premium indicators — unlocked from the Starter tier up.
STARTER_INDICATORS = {
    "rsi": "Relative Strength Index",
    "macd": "MACD",
    "bbands": "Bollinger Bands",
    "vwap": "VWAP",
}

# Advanced indicators — Pro tier only.
PRO_INDICATORS = {
    "stoch": "Stochastic",
    "atr": "Average True Range",
    "fib": "Fibonacci Retracement",
    "ichimoku": "Ichimoku Cloud",
}

# Which indicator group each tier unlocks (cumulative).
INDICATOR_TIERS = {
    "free": FREE_INDICATORS,
    "starter": STARTER_INDICATORS,
    "pro": PRO_INDICATORS,
}

# Back-compat alias for any caller still importing the old name.
PREMIUM_INDICATORS = {**STARTER_INDICATORS, **PRO_INDICATORS}
ALL_INDICATORS = {**FREE_INDICATORS, **PREMIUM_INDICATORS}


def entitlements_for(unlocked_tiers) -> dict:
    """Indicator catalog annotated with unlocked state for a plan.

    `unlocked_tiers` is the list of indicator tiers the plan unlocks, e.g.
    ["free", "starter"]. Indicators in those tiers come back unlocked; the rest
    locked, so the frontend can show them as upsell options.
    """
    unlocked = set(unlocked_tiers)
    indicators = []
    for tier, slugs in INDICATOR_TIERS.items():
        is_unlocked = tier in unlocked
        for slug, label in slugs.items():
            indicators.append(
                {"slug": slug, "label": label, "unlocked": is_unlocked, "tier": tier}
            )
    return {"indicators": indicators}
