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
      "Stochastic, ATR, Fibonacci & Ichimoku Cloud",
      "Watchlist of 150 coins, set up for you",
      "Every signal strategy followed by default",
      "Unlimited daily signals",
      "Telegram signal alerts",
      "Save up to 50 chart layouts",
    ],
  },
];
