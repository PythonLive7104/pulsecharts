"""Curated knowledge base for the landing-page support chat.

Deliberately NOT an LLM. Answers are written from PulseCharts project facts
(CLAUDE.md) and matched to a visitor's question by keyword overlap. Anything the
base can't confidently match falls back to the contact-us flow (see views.py).

To extend: add an entry with distinctive `keywords` and a concise `answer`. Keep
keywords lowercase; matching lowercases the incoming message. Multi-word keyword
phrases ("financial advice") score higher than single words, which biases toward
the more specific topic when several entries partially match.
"""

# Shown as starter chips before the visitor types anything, and as a nudge after
# an unmatched question. Each must be answerable by an entry below.
SUGGESTED_QUESTIONS = [
    "What is PulseCharts?",
    "Do you support forex?",
    "Is it free?",
    "What's included in premium?",
    "Do you offer trading signals?",
]

# Each entry: keywords (any-match, scored) -> answer.
KNOWLEDGE = [
    {
        "id": "about",
        "keywords": ["what is", "pulsecharts", "what does", "purpose", "what's this", "what is this", "tell me about"],
        "answer": (
            "PulseCharts is a TradingView-style charting app for crypto AND forex "
            "traders, powered by Hyperliquid's live crypto data and live forex "
            "feeds. You get real-time candlestick charts on every timeframe for "
            "free, and a premium plan unlocks advanced indicators, saved layouts, "
            "and trading signals on both markets."
        ),
    },
    {
        "id": "data_source",
        "keywords": ["data", "price", "where", "source", "feed", "hyperliquid", "real-time", "realtime", "live data", "latency"],
        "answer": (
            "Live market data comes from Hyperliquid's public WebSocket feed, "
            "relayed through our servers so your browser gets a single low-latency "
            "stream. It's crypto-only by design — no forex or stocks."
        ),
    },
    {
        "id": "free",
        "keywords": ["free", "cost nothing", "no cost", "free tier", "free forever", "is it free", "really free"],
        "answer": (
            "Yes — the free tier is genuinely free, no card required. It includes "
            "live charts, every timeframe, unlimited symbol switching, the "
            "SMA/EMA/Volume indicators, and a basic watchlist. Premium adds the "
            "advanced indicators and more."
        ),
    },
    {
        "id": "premium",
        "keywords": ["premium", "paid", "pro", "starter", "upgrade", "subscription", "plan", "plans", "what's included", "benefits"],
        "answer": (
            "Premium unlocks the advanced indicators (RSI, MACD, Bollinger Bands, "
            "Stochastic, ATR, Fibonacci, VWAP, Ichimoku Cloud), multiple saved "
            "chart layouts, an expanded watchlist, and the trading-signals feed. "
            "You can upgrade any time from your dashboard."
        ),
    },
    {
        "id": "pricing",
        "keywords": ["price", "pricing", "how much", "cost", "fee", "monthly", "per month", "expensive", "cheap"],
        "answer": (
            "PulseCharts is priced well below TradingView's indicator tiers — "
            "it's built as the affordable, crypto-focused alternative. You can see "
            "the current plan prices in the Pricing section on this page, and start "
            "free with no card required."
        ),
    },
    {
        "id": "indicators",
        "keywords": ["indicator", "indicators", "rsi", "macd", "bollinger", "ema", "sma", "vwap", "ichimoku", "stochastic", "atr", "fibonacci"],
        "answer": (
            "Free indicators: SMA, EMA, and Volume. Premium indicators: RSI, MACD, "
            "Bollinger Bands, Stochastic, ATR, Fibonacci Retracement, VWAP, and "
            "Ichimoku Cloud. All are computed live against the chart you're viewing."
        ),
    },
    {
        "id": "coins",
        "keywords": ["coin", "coins", "symbol", "symbols", "which crypto", "tokens", "btc", "eth", "altcoin"],
        "answer": (
            "On crypto we chart the perps and spot pairs listed on Hyperliquid, "
            "with more added over time. We also now support the major forex pairs "
            "— flip between crypto and forex with the toggle above the chart."
        ),
    },
    {
        "id": "forex",
        "keywords": ["forex", "fx", "currency", "currencies", "eur", "usd", "gbp", "jpy", "eurusd", "euro", "dollar", "pound", "yen", "pairs", "chf", "aud", "cad", "nzd"],
        "answer": (
            "Yes — forex is now live! 🎉 We support the major pairs: EUR/USD, "
            "GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD and NZD/USD. Use the "
            "Crypto/Forex toggle above the chart to switch markets. The same "
            "indicators and trading signals work on forex too."
        ),
    },
    {
        "id": "timeframes",
        "keywords": ["timeframe", "timeframes", "interval", "candle", "1m", "5m", "1h", "4h", "1d", "daily"],
        "answer": (
            "All standard timeframes are available on every plan, from 1-minute "
            "up to daily candles — switch between them with one click."
        ),
    },
    {
        "id": "signals",
        "keywords": ["signal", "signals", "buy sell", "trade signal", "alerts", "strategy", "strategies", "confidence"],
        "answer": (
            "Trading signals are a premium feature: algorithmic strategies scan "
            "tracked crypto AND forex pairs and surface buy/sell setups with entry, "
            "stop-loss, take-profit targets, and a confidence score. They're "
            "informational only — not financial advice."
        ),
    },
    {
        "id": "advice",
        "keywords": ["financial advice", "advice", "recommendation", "should i buy", "guarantee", "accurate", "accuracy", "profit", "make money", "risk"],
        "answer": (
            "No part of PulseCharts is financial advice. It's a charting and "
            "analysis tool — nothing here is a recommendation to buy or sell, and "
            "signals are algorithmic, informational output. Always do your own "
            "research and manage your own risk."
        ),
    },
    {
        "id": "signup",
        "keywords": ["sign up", "signup", "register", "get started", "create account", "join", "start"],
        "answer": (
            "Click \"Get started\" at the top of the page to create a free account "
            "— no card required. You'll be charting live in under a minute."
        ),
    },
    {
        "id": "layouts",
        "keywords": ["layout", "layouts", "save", "saved", "presets", "workspace"],
        "answer": (
            "Saved chart layouts (symbol + timeframe + indicator setup) are a "
            "premium feature, so you can jump straight back into your setups across "
            "devices. The free tier keeps a single active chart."
        ),
    },
    {
        "id": "watchlist",
        "keywords": ["watchlist", "watch list", "favourite", "favorite", "track coins"],
        "answer": (
            "Every account gets a watchlist to save and reorder symbols. The free "
            "tier has a capped number of symbols; premium expands it."
        ),
    },
    {
        "id": "telegram",
        "keywords": ["telegram", "notification", "notifications", "push", "bot"],
        "answer": (
            "Premium members can connect a Telegram bot to receive new trading "
            "signals as push messages. You link it from your dashboard once "
            "you're on a premium plan."
        ),
    },
    {
        "id": "billing",
        "keywords": ["billing", "payment", "pay", "card", "refund", "cancel", "cancellation", "invoice", "dodo"],
        "answer": (
            "Payments are handled securely by Dodo Payments. You can manage or "
            "cancel your subscription from your dashboard — when you cancel you keep "
            "access until the end of the period you've paid for. For specific "
            "billing questions, contact us below."
        ),
    },
    {
        "id": "account",
        "keywords": ["password", "reset password", "forgot", "login", "log in", "can't log in", "account", "email change"],
        "answer": (
            "You can reset your password from the Sign in page via \"Forgot "
            "password\" — we'll email you a secure reset link. For anything else "
            "account-related, reach out through the contact option below."
        ),
    },
    {
        "id": "mobile",
        "keywords": ["mobile", "phone", "app store", "android", "ios", "responsive", "tablet"],
        "answer": (
            "PulseCharts is a responsive web app that works in your phone's browser "
            "— there's no separate native app to install. Just open the site and "
            "sign in."
        ),
    },
    {
        "id": "greeting",
        "keywords": ["hi", "hello", "hey", "good morning", "good afternoon", "yo", "help"],
        "answer": (
            "Hi! I can answer questions about PulseCharts — pricing, features, "
            "indicators, data, signals, and more. What would you like to know?"
        ),
    },
]


def answer_question(message: str):
    """Return (answer, matched, suggestions).

    matched=False means nothing scored, so the caller should steer the visitor to
    the contact-us flow. Scoring: each keyword found as a substring scores 1, plus
    a bonus for multi-word phrases so specific topics win ties.
    """
    text = (message or "").lower().strip()
    if not text:
        return (
            "Ask me anything about PulseCharts and I'll do my best to help.",
            False,
            SUGGESTED_QUESTIONS,
        )

    best, best_score = None, 0
    for entry in KNOWLEDGE:
        score = 0
        for kw in entry["keywords"]:
            if kw in text:
                score += 2 if " " in kw else 1
        if score > best_score:
            best, best_score = entry, score

    # "greeting" alone shouldn't block a real question — require a real topic
    # match (score >= 1) and let non-greeting topics outrank a bare "hi".
    if best is None or best_score == 0:
        return (
            "I'm not sure I have an answer for that one. You can send your "
            "question to our team using the contact option below and we'll get "
            "back to you by email.",
            False,
            SUGGESTED_QUESTIONS,
        )

    return (best["answer"], True, [])
