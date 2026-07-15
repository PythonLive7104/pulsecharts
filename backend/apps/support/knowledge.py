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
    "Is there a lifetime deal?",
    "Do you offer trading signals?",
    "Why no signals on weekends?",
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
            "Crypto market data comes from Hyperliquid's public WebSocket feed, "
            "relayed through our servers so your browser gets a single low-latency "
            "stream. Forex pairs run on a live FX feed alongside it — flip between "
            "the two markets with the Crypto/Forex toggle above the chart."
        ),
    },
    {
        "id": "free",
        "keywords": ["free", "cost nothing", "no cost", "free tier", "free forever", "is it free", "really free"],
        "answer": (
            "Yes — the free tier is genuinely free, no card required. It includes "
            "live crypto & forex charts, every timeframe, unlimited symbol "
            "switching, the SMA/EMA/Volume indicators, a 20-coin watchlist, 4 signal "
            "strategies, and a taste of trading signals (up to 20/week). Starter and "
            "Pro add the advanced indicators, more signals, and Telegram alerts."
        ),
    },
    {
        "id": "premium",
        "keywords": ["premium", "paid", "pro", "starter", "upgrade", "subscription", "plan", "plans", "what's included", "benefits"],
        "answer": (
            "There are two paid tiers. Starter ($9) adds RSI, MACD, Bollinger "
            "Bands and VWAP, up to 400 signals/week, Telegram alerts, 6 signal "
            "strategies, and 10 saved layouts. Pro ($19) unlocks every indicator "
            "(Stochastic, ATR, Fibonacci, Ichimoku Cloud), unlimited signals, "
            "build-your-own-strategy with AI, and a 150-coin watchlist. Each is a "
            "one-time payment for 30 days of access. Prefer to pay once? There's also "
            "a Pro Lifetime option — ask me about the lifetime deal."
        ),
    },
    {
        "id": "pricing",
        "keywords": ["price", "pricing", "how much", "cost", "fee", "monthly", "per month", "expensive", "cheap"],
        "answer": (
            "Three plans: Free ($0, no card required), Starter ($9) and Pro ($19). "
            "Starter and Pro are one-time payments that unlock 30 days of access — "
            "no auto-renewal, no card kept on file. There's also a one-off Pro "
            "Lifetime for $67 (25% off the usual $89) that never expires. It's priced "
            "well below TradingView's indicator tiers as the affordable crypto & "
            "forex alternative."
        ),
    },
    {
        # Prices here are kept in step with apps/accounts/plans.py (LIFETIME_PLAN).
        # If that price changes, update this answer too — the chat is prose, not
        # wired to the plan dict.
        "id": "lifetime",
        "keywords": [
            "lifetime", "life time", "pay once", "one time", "one-time", "forever",
            "lifetime deal", "lifetime plan", "buy once", "own it", "never expires",
        ],
        "answer": (
            "Yes! Pro Lifetime is a single payment of $67 — that's 25% off the usual "
            "$89 — and it never expires. You get every Pro feature for life: all "
            "indicators, unlimited signals on crypto & forex, Telegram alerts, "
            "build-your-own-AI-strategy, a 150-coin watchlist and 50 saved layouts, "
            "with nothing to renew. Grab it from the pricing section or your billing "
            "page while the discount lasts."
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
        "keywords": ["signal", "signals", "buy sell", "trade signal", "alerts", "strategy", "strategies", "confidence", "take profit", "stop loss", "tp1", "tp2", "tp3"],
        "answer": (
            "Algorithmic strategies scan tracked crypto AND forex pairs and surface "
            "buy/sell setups with an entry, a stop-loss, three take-profit targets "
            "(TP1/TP2/TP3) and a conviction score. A setup only surfaces when several "
            "strategies agree on it. The idea is to bank a partial at each target and "
            "move your stop to break-even after TP1; you also get an update if a "
            "trade's trend flips and the setup is invalidated. Free gets a taste (up "
            "to 20/week), Starter up to 400/week, and Pro unlimited — with Telegram "
            "alerts on the paid tiers. They're informational only, not financial advice."
        ),
    },
    {
        "id": "weekend",
        "keywords": [
            "weekend", "weekends", "saturday", "sunday", "no signals", "no new signals",
            "signals stopped", "not getting signals", "market closed", "why no signal",
        ],
        "answer": (
            "The engine pauses NEW signals over the weekend (from Friday evening to "
            "Sunday evening UTC), for crypto and forex alike. Weekend sessions are "
            "thin and choppy — they produce fakeouts that trip stops — so we sit them "
            "out rather than send low-quality setups. Your open trades are still "
            "tracked the whole time: TP hits, stop-losses and trend-invalidations "
            "keep updating in-app and on Telegram. Fresh signals resume when the "
            "market reopens Sunday evening."
        ),
    },
    {
        "id": "custom_strategy",
        "keywords": [
            "custom strategy", "custom strategies", "build strategy", "build your own",
            "create strategy", "create a strategy", "own strategy", "my own strategy",
            "ai strategy", "build with ai", "strategy builder", "make a strategy",
        ],
        "answer": (
            "Yes — Pro members can build their own signal strategy with our built-in "
            "AI. Just describe the setup you want in plain English (e.g. \"buy when "
            "RSI is oversold and price is above the 200 EMA\") and the AI turns it "
            "into a working strategy that scans your watchlist and generates signals "
            "like any built-in one. Pro includes up to 5 custom strategies per month."
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
            "Starter and Pro members can connect a Telegram bot to receive new "
            "trading signals as push messages, capped by their plan's weekly limit "
            "(400/week on Starter, unlimited on Pro). You link it from your dashboard "
            "once you're on a paid plan."
        ),
    },
    {
        "id": "billing",
        "keywords": ["billing", "payment", "pay", "card", "refund", "cancel", "cancellation", "invoice", "paystack", "dodo"],
        "answer": (
            "Payments are handled securely by Paystack. Each plan is a one-time "
            "payment that unlocks 30 days of access — nothing auto-renews and no "
            "card is stored, so there's nothing to cancel; access simply ends when "
            "the 30 days are up unless you pay again. For specific billing "
            "questions, contact us below."
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
