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
      "1 signal strategy followed for you",
      "Up to 5 signals/day",
    ],
  },
  {
    key: "starter", label: "Starter", price_usd: 9, period: "mo",
    tagline: "Core indicators and a real signal feed.",
    features: [
      "Everything in Free",
      "RSI, MACD, Bollinger Bands & VWAP",
      "Watchlist of 40 coins, set up for you",
      "4 signal strategies followed by default",
      "Up to 30 signals/day",
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
      "Unlimited daily signals",
      "Telegram signal alerts",
      "Save up to 50 chart layouts",
    ],
  },
];
