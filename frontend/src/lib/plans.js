// Plan tiers, least to most privileged (mirrors PLAN_ORDER in plans.py). Used to
// gate plan-restricted resources like Pro-only symbols.
export const PLAN_ORDER = ["free", "starter", "pro"];

export function planRank(key) {
  const i = PLAN_ORDER.indexOf(key === "premium" ? "pro" : key);
  return i === -1 ? 0 : i;
}

// True if `planKey` meets the `minPlan` requirement (blank/unknown min = free).
export function planAllows(planKey, minPlan) {
  return planRank(planKey || "free") >= planRank(minPlan || "free");
}

// True only for users who BOUGHT the lifetime plan. Pricing sections hide on this,
// since there's nothing left to sell them.
export function isLifetime(entitlements) {
  return Boolean(entitlements?.is_lifetime);
}

// True whenever paid access never expires — a lifetime purchase OR a staff-granted
// perpetual plan. Broader than isLifetime(): a staff-granted Pro still sees pricing,
// but can't redeem a code or credits (a timed grant would overwrite their null
// expiry and downgrade them), so the redeem surfaces hide on this instead.
export function planNeverExpires(entitlements) {
  return Boolean(entitlements?.plan_never_expires);
}

// Mirrors LIFETIME_PLAN in backend apps/accounts/plans.py. A purchase option, not
// a tier — buying it grants Pro with no expiry.
export const LIFETIME_FALLBACK = {
  key: "lifetime", label: "Pro Lifetime", price_usd: 67,
  original_price_usd: 89, discount_pct: 25, period: "once",
  tagline: "Every Pro feature, forever. One payment, no renewals.",
  features: [
    "Everything in Pro, for life",
    "One payment — never expires, never renews",
    "Build your own strategy with AI (up to 5/mo)",
    "Every indicator: Stochastic, ATR, Fibonacci & Ichimoku Cloud",
    "Watchlist of 150 coins, set up for you",
    "Unlimited signals + Telegram alerts",
    "Save up to 50 chart layouts",
  ],
};

// Static plan catalog mirroring backend apps/accounts/plans.py. Used as a
// fallback so pricing/billing UIs render the three tiers even if /api/plans/ is
// unreachable; the live endpoint (api.plans()) is the source of truth at runtime.
export const PLAN_FALLBACK = [
  {
    key: "free", label: "Free", price_usd: 0, period: "mo",
    tagline: "Live crypto charts and a taste of signals.",
    features: [
      "Live candlestick charts, all timeframes",
      "SMA, EMA & Volume overlays",
      "Starter watchlist of 20 coins, ready to go",
      "4 signal strategies followed for you",
      "Up to 20 signals/week",
    ],
  },
  {
    key: "starter", label: "Starter", price_usd: 9, period: "mo",
    tagline: "Core indicators and a real signal feed.",
    features: [
      "Everything in Free",
      "RSI, MACD, Bollinger Bands & VWAP",
      "Watchlist of 40 coins, set up for you",
      "6 signal strategies followed by default",
      "Up to 400 signals/week",
      "Telegram signal alerts",
      "Save up to 10 chart layouts",
    ],
  },
  {
    key: "pro", label: "Pro", price_usd: 19, period: "mo",
    tagline: "Every indicator and strategy, unlimited signals.",
    features: [
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
];
