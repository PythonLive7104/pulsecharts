# PulseCharts — Project Context for Claude Code

This file is meant to live at the project root so Claude Code reads it
automatically at the start of every session. It captures everything
decided so far. Treat anything marked "TBD / open decision" as not
yet final — confirm with the developer before building around it.

## 1. What This Project Is

A TradingView-style web app for retail crypto traders, powered by
Hyperliquid's market data. Free tier gives live charting; premium
tier (paid subscription) unlocks advanced technical indicators and
saved layouts. Built and shipped solo. Forex was considered and
dropped — crypto-only for v1. A paid trading-signals feature is
planned for v2 (Section 13) — documented now, not part of the MVP build.

## 2. Developer Context

Solo indie developer / AI engineering student, based in Lagos,
Nigeria, operating under MAILIONDEV TECHNOLOGY LTD (RC 9233525).
Ships multiple SaaS products in parallel — existing products include
InvoiceParsed (Flask/React/Supabase, AI document extraction) and
BounceTrap (Django, email verification). This project intentionally
uses Django + DRF (not Flask) and Postgres directly (not Supabase),
so don't assume patterns from the other products carry over unless
explicitly stated here. Dodo Payments is the billing provider already
in use elsewhere and is being reused here for subscriptions.

## 3. Tech Stack (confirmed)

- Frontend: React 18, lightweight-charts (TradingView's open-source
  charting library) for rendering candles/lines/volume.
- State management: Zustand or Redux Toolkit (TBD — pick whichever
  the dev is more comfortable with; Zustand is lighter for this scope).
- Backend: Django 5 + Django REST Framework.
- Real-time layer: Django Channels + Redis (websocket relay and
  pub/sub between the Hyperliquid feed and connected browser clients).
- Database: PostgreSQL.
- Market data source: Hyperliquid public websocket API (no auth
  needed for market data subscriptions). Single source — no separate
  forex feed required.
- Billing: Dodo Payments (already integrated for InvoiceParsed —
  reuse merchant account once payouts are sorted).
- Indicator computation: client-side in React/JS against the local
  candle buffer (no backend indicator math at MVP scope).
- Celery + Celery Beat: not needed for the MVP itself, but becomes a
  real dependency once the v2 signals feature (Section 13) ships,
  since signal generation has to run continuously server-side.

## 4. Proposed Repo Structure

```
/backend
  /config            # Django project settings, asgi.py for Channels
  /apps
    /accounts         # auth, user model extensions, entitlements
    /billing          # Dodo Payments checkout + webhook handling
    /market_data      # Symbol model, candle normalization, relay consumer
    /watchlists
    /chart_layouts
    /signals          # v2 — signal services, generation, delivery (Section 13)
  manage.py
/frontend
  /src
    /components       # Chart, IndicatorPicker, Watchlist, SymbolSearch
    /hooks             # useMarketSocket, useEntitlements
    /lib               # indicator math (sma, ema, rsi, macd, bbands, etc.)
    /store             # Zustand/Redux store
    App.jsx
```

## 5. MVP Scope

In scope:
- Live candlestick charts for Hyperliquid-listed coins, multiple
  timeframes.
- Free indicators: SMA, EMA, Volume.
- Premium indicators (gated): RSI, MACD, Bollinger Bands, Stochastic,
  ATR, Fibonacci Retracement, VWAP, Ichimoku Cloud.
- Watchlist (save/reorder symbols).
- Saved chart layouts (symbol + timeframe + indicator config).
- Auth + Dodo Payments subscription billing.
- Entitlements endpoint controlling which indicators a user sees.

Explicitly out of scope for MVP (don't build unless asked):
- Price alerts/notifications.
- Drawing tools (trendlines, shapes).
- Backtesting or paper trading.
- Native mobile app — responsive web only.
- Social features (sharing charts, public layouts).
- Forex — deliberately dropped for v1; revisit only if there's
  demand once crypto v1 has traction.
- Trading signals (buy/sell signal feed with selectable strategies)
  — fully spec'd as a v2 feature in Section 13, intentionally kept
  out of the MVP build to protect the lean timeline.

## 6. Real-Time Data Layer — Confirmed Protocol Details

### 6.1 Hyperliquid — verified against official docs

- Mainnet WS endpoint: `wss://api.hyperliquid.xyz/ws`
- Testnet WS endpoint: `wss://api.hyperliquid-testnet.xyz/ws`
- No auth required for public market data subscriptions (auth only
  needed for user-specific streams like fills/orders, which this
  project doesn't need).
- Subscribe message:
  ```json
  { "method": "subscribe", "subscription": { "type": "candle", "coin": "BTC", "interval": "1m" } }
  ```
- Supported intervals (per docs): 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h,
  8h, 12h, 1d (some sources list up to 1w/1M — verify against the
  live docs at build time since this has shifted across SDK versions).
- Unsubscribe uses the same shape with `"method": "unsubscribe"`.
- Server acknowledges subscriptions with:
  ```json
  { "channel": "subscriptionResponse", "data": { "method": "subscribe", "subscription": {...} } }
  ```
- Candle payload fields seen across SDKs: `t`, `T`, `s`, `i`, `o`,
  `c`, `h`, `l`, `v`, `n` (symbol, interval, OHLCV, trade count, and
  two timestamp fields). Caution: unofficial SDKs disagree on which
  of `t`/`T` is open-time vs close-time — confirm directly against
  the live Hyperliquid WebSocket docs (hyperliquid.gitbook.io) when
  implementing the parser rather than trusting any single SDK.
- All clients should handle disconnects and reconnect gracefully —
  Hyperliquid explicitly warns connections can drop without notice.
- Other useful subscription types if needed later: `trades`, `allMids`
  (all mid prices in one stream), `l2Book` (order book depth).
- Coverage check: Hyperliquid lists perps and a growing set of spot
  pairs, but it's not exhaustive of every coin a trader might want
  charted. Confirm the planned symbol list is actually available on
  Hyperliquid before locking the watchlist/symbol-search UI around it.

### 6.2 Internal normalized format

Even with a single upstream source, normalize candles into one
internal shape before they reach the frontend or the Channels
broadcast layer — this keeps the door open to adding another data
source later without reworking the frontend:
```json
{ "symbol": "BTC-USD", "time": 1750000000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1 }
```

## 7. WebSocket Relay Architecture

The backend service maintains a single upstream connection to
Hyperliquid, subscribing only to symbols with at least one active
client watching them. Normalized ticks/candles are pushed into
Redis-backed Channels groups keyed by symbol. Browser clients connect
once to the app's own `/ws/market/` endpoint and subscribe/unsubscribe
to symbol groups as the user switches charts. This keeps the upstream
connection count low regardless of concurrent users on the same
symbol, and centralizes reconnect/backoff logic in one place instead
of every browser tab managing its own Hyperliquid connection.

## 8. Data Model (high-level, MVP)

- `User` (Django auth, extended): email, plan_tier, plan_expiry,
  dodo_customer_id
- `Subscription`: user_id, tier, status, renewal_date, payment_ref
- `Symbol`: ticker, display_name (kept as its own model rather than
  hardcoded in code, so adding/removing tracked coins doesn't need a
  deploy)
- `WatchlistItem`: user_id, symbol_id, sort_order
- `ChartLayout`: user_id, symbol_id, timeframe, indicator_config
  (JSON), saved_at

See Section 13 for the additional v2 data model needed for signals —
not part of the MVP migration set.

## 9. API Endpoints (representative, MVP)

```
GET    /api/symbols/                    list available symbols
GET    /api/symbols/{symbol}/candles/    historical OHLCV (REST, initial chart load)
GET    /api/watchlist/                  user's watchlist
POST   /api/watchlist/                  add symbol to watchlist
DELETE /api/watchlist/{id}/             remove symbol
GET    /api/me/entitlements/            current plan + unlocked indicators
GET    /api/chart-layouts/              list saved layouts
POST   /api/chart-layouts/              save a layout
POST   /api/billing/checkout/           create Dodo Payments checkout session
POST   /api/billing/webhook/            Dodo Payments webhook handler
WS     /ws/market/                      real-time candle stream, topic-subscribed per symbol
```

## 10. Indicator Engine

All indicators in MVP scope (SMA, EMA, RSI, MACD, Bollinger Bands,
Stochastic, ATR, Fibonacci Retracement, VWAP, Ichimoku Cloud) are
computed client-side against the candle buffer already driving
lightweight-charts. No backend computation needed at this stage.

Important framing for premium positioning: these indicators are all
derived from public OHLCV data, so they cannot be technically
"protected" the way proprietary data could be — anyone could compute
them independently. The premium value proposition here is convenience
and curation (one-click access, saved presets, no setup), not data
secrecy. Worth keeping that in mind for pricing/marketing copy. (Note
this caveat does NOT apply to the v2 signals feature in Section 13 —
a generated signal is this product's own output, not raw public data,
so it can legitimately be gated server-side.)

## 11. Premium Gating

- `/api/me/entitlements/` returns the user's plan and the indicator
  set unlocked for it.
- Frontend hides/locks premium indicator options based on that
  response, re-checked each session.
- Since the underlying protection is UI-level rather than data-level
  (see Section 10), don't over-invest in obfuscation — focus effort
  on the gating UX being clean and the upgrade flow being frictionless.
- The v2 signals feature (Section 13) reuses this same entitlements
  mechanism but enforces its quota server-side, since signal output
  is real product value worth actually protecting.

## 12. Feature Tiers

**Free**: live crypto charts, all timeframes, SMA/EMA/Volume
overlays, unlimited symbol switching (one active chart), basic
watchlist with a capped symbol count.

**Premium** (Dodo Payments subscription): RSI, MACD, Bollinger Bands,
Stochastic, ATR, Fibonacci Retracement, VWAP, Ichimoku Cloud, multiple
saved chart layouts, expanded watchlist size. Price alerts are a
strong v2 candidate, not MVP. Trading signals (Section 13) are also
v2, and will likely become a key premium-tier differentiator once
they ship — factor that into pricing once the feature is closer to
real.

Pricing anchor for discussion (not final): aim meaningfully below
TradingView's indicator-tier pricing to reinforce the "affordable
alternative" positioning. Dropping forex removes the only real data
cost in the original plan (Hyperliquid's market data is free), so
MVP margin is mostly a function of Dodo Payments' processing fees,
not data costs. Signal-related infra cost (Celery workers, more
compute) is a v2 consideration, not an MVP one.

## 13. Trading Signals — v2 Feature (documented now, not built in MVP)

Confirmed direction from the developer: in-app delivery only for v2
(push notifications and email are a later phase, not scoped here),
and "signal service" means multiple distinct strategies a user can
choose to follow — not one black-box score.

### 13.1 Concept

For each tracked Hyperliquid symbol, the backend continuously
evaluates a set of algorithmic strategies ("signal services") and
produces buy/sell calls with a confidence percentage. Users pick
which strategies to follow; their personal signal feed only shows
output from services they've subscribed to, filtered to "high
percentage" calls above a minimum confidence threshold so the feed
isn't flooded with weak/noisy signals. How many signals a user sees
per day is capped by their subscription tier.

### 13.2 Signal services (starting set to refine, not committed)

- **Momentum Crossover** — EMA crossover confirmed by RSI moving in
  the same direction.
- **MACD Trend Following** — signal-line crossovers combined with
  histogram strength.
- **Bollinger Band Mean Reversion** — price touching/exceeding a band
  combined with RSI at an extreme.
- **Volatility Breakout** — ATR expansion combined with a break of a
  recent price range.

Each strategy run produces, per symbol/timeframe: a direction
(buy/sell), a confidence percentage (0–100, derived from how many of
the strategy's sub-conditions align and by how much), the price at
signal time, and a timestamp.

### 13.3 Daily quota by plan

Reuses the entitlements mechanism from Section 11, extended with a
`signal_daily_quota` field. Shape to refine, not final numbers yet:
free tier sees little to no signal feed (or a small daily teaser),
mid premium tier gets a meaningful daily cap, a top tier gets a high
or unlimited cap. Quota must be enforced server-side (track what's
been delivered to each user each day) rather than just hidden in the
UI, since — unlike chart indicators — signal output is this product's
own generated content and is worth actually protecting.

### 13.4 Data model additions (v2 — not part of the MVP migrations)

- `SignalService`: id, name, slug, description, strategy_type,
  is_active
- `Signal`: id, symbol_id, service_id, direction, confidence_pct,
  price_at_signal, timeframe, generated_at
- `UserSignalSubscription`: user_id, service_id, subscribed_at —
  which strategies a given user follows
- `SignalDelivery`: user_id, signal_id, delivered_at — backs daily
  quota enforcement and prevents re-showing the same signal twice

### 13.5 API endpoints (v2)

```
GET    /api/signal-services/                 list available strategies
POST   /api/me/signal-subscriptions/         follow a strategy
DELETE /api/me/signal-subscriptions/{id}/    unfollow a strategy
GET    /api/me/signals/feed/                 personalized feed, capped by plan quota
```
(`/api/me/entitlements/` gains a `signal_daily_quota` field.)

### 13.6 Why this needs backend compute (unlike indicators)

Chart indicators (Section 10) only need to exist while a user is
actively looking at a chart, so client-side math is enough. Signals
have to exist independent of whether anyone's looking — they need to
be generated continuously in the background and stored, so a user
opening the app sees signals that already happened. That's the actual
justification for Celery + Celery Beat (noted as a dependency in
Section 3): scheduled tasks evaluate each active strategy against
each tracked symbol on a recurring basis and write new `Signal` rows
when conditions trigger.

### 13.7 Flags before building this (read before starting, not optional)

- **Backtest every strategy against historical data before launch**,
  and before using language like "high percentage" anywhere in the
  product or marketing — don't ship accuracy claims that haven't
  actually been validated against real price history.
- **This is paid trading signals**, which in a number of jurisdictions
  brushes up against investment-advice regulation. At minimum, ship
  clear disclaimers that signals are informational/algorithmic output,
  not financial advice, and don't word anything as a recommendation
  to buy or sell. A quick legal read before charging money for this
  is worth doing separately from the technical build — this is a
  business risk, not just an engineering one.
- Push notification and email delivery (the longer-term plan per the
  developer) will need a provider decision later — a transactional
  email service plus either Web Push or a service like OneSignal.
  Not scoped here; revisit when that phase actually starts.

## 14. MVP Build Plan (3-4 weeks, solo dev — signals NOT included)

Dropping forex removes what was originally a "Week 3: FX integration"
step; signals (Section 13) are intentionally excluded from this plan
entirely, not just deferred a week — they're a v2 feature with their
own build phase once the MVP ships.

- Week 1: Django + DRF scaffold, auth, Postgres setup. React app
  shell. Hyperliquid WS prototype — connect and log live candle data.
- Week 2: End-to-end candle pipeline (Hyperliquid → Channels relay →
  React + lightweight-charts). Symbol search/switch. Watchlist
  feature. Timeframe switching.
- Week 3: Free indicators (SMA, EMA, Volume) client-side. Premium
  indicator UI scaffolding with locked state. Entitlements endpoint.
  Dodo Payments checkout + webhook integration.
- Week 4: Unlock premium indicator set (RSI, MACD, Bollinger Bands
  minimum, expand to the rest if time allows). Saved chart layouts.
  Polish, bug bash, landing page, launch prep.

## 15. Environment Variables (expected, fill in as decided)

```
DJANGO_SECRET_KEY=
DATABASE_URL=
REDIS_URL=
HYPERLIQUID_WS_URL=wss://api.hyperliquid.xyz/ws
DODO_PAYMENTS_API_KEY=
DODO_PAYMENTS_WEBHOOK_SECRET=
FRONTEND_URL=
```
(Signals-related env vars — e.g. a notification provider key — will
be added once that v2 phase starts, see Section 13.7.)

## 16. Risks & Mitigations

- Hyperliquid can drop connections without warning — reconnect/backoff
  must be built into the relay from the start, not bolted on later.
- Hyperliquid's symbol coverage (perps + growing spot list) may not
  include every coin traders expect — confirm the target symbol list
  is actually listed there before finalizing the watchlist/search UI.
- Indicators aren't technically protectable (Section 10) — premium
  value rests on UX/curation, factor that into pricing expectations.
- Dropping forex narrows the addressable market versus TradingView's
  full asset breadth — worth being explicit in marketing copy that
  this is a crypto-focused tool, not a general one, so expectations
  are set correctly.
- The planned signals feature (Section 13) carries real
  regulatory/liability risk if launched without disclaimers or
  backtested accuracy claims — see Section 13.7, don't skip it.
- Dodo Payments merchant onboarding for MAILIONDEV TECHNOLOGY LTD was
  still being finalized as of the last conversation about it (TIN
  validation, corporate bank account) — confirm that's resolved before
  premium billing goes live, or gate the premium tier behind a
  "coming soon" flag if payouts aren't ready by launch.

## 17. Open Decisions (resolve before/during build, not blockers to starting)

- Zustand vs Redux Toolkit for frontend state.
- Final premium price point and whether to offer annual billing.
- Whether price alerts pull into MVP or stay strictly v2.
- Mobile responsiveness bar for launch (usable-on-phone vs.
  desktop-first with mobile as a fast-follow).
- Final product name (currently "PulseCharts," a placeholder).
- How to use the time freed up by dropping forex.
- Signals (Section 13): exact confidence-score methodology per
  strategy, the "high percentage" threshold, final daily quota
  numbers per plan tier, and the legal/disclaimer review — none of
  these block starting the MVP, but all need answers before Section
  13 moves from documented to built.

## 18. Success Metrics

Free signups (weekly), free-to-paid conversion rate, MRR, weekly
active chart sessions, monthly churn rate, average premium
indicators enabled per paying user. Once signals ship: signal feed
engagement (opens per day), conversion lift attributable to signal
access, and — critically — realized signal accuracy over time,
tracked honestly even if it's not flattering.


## 19. Trading Signal Structure — Full Signal Card Spec

Each generated signal (Section 13) must include the following fields.
This is what renders on the user's dashboard signal card.

### 19.1 Signal fields

```
symbol          e.g. "BTC", "ETH"
direction       "BUY" | "SELL"
confidence_pct  0–100 integer (only signals >= 65 surface to users)
timeframe       e.g. "1h", "4h", "1d"
generated_at    ISO timestamp

entry_price     float — the price at which the signal was generated
stop_loss       float — price where the thesis is invalidated

tp1             float — conservative target (~1:1 risk/reward)
tp2             float — standard target  (~1:2 risk/reward)
tp3             float — extended target  (~1:3 risk/reward)
tp4             float — runner target    (~1:4–1:5 risk/reward, near next major S/R)

risk_pct        float — % distance from entry to stop loss
                  e.g. entry=100, sl=95 → risk_pct = 5.0
reward_tp1_pct  float — % gain from entry to TP1
reward_tp2_pct  float — % gain from entry to TP2
reward_tp3_pct  float — % gain from entry to TP3
reward_tp4_pct  float — % gain from entry to TP4

risk_reward_tp1 float — ratio e.g. 1.0  (reward / risk)
risk_reward_tp2 float — ratio e.g. 2.0
risk_reward_tp3 float — ratio e.g. 3.1
risk_reward_tp4 float — ratio e.g. 4.6

dollar_risk     float — how much a user loses if SL is hit (per $100 trade size)
dollar_tp1      float — profit at TP1 per $100 trade size
dollar_tp2      float — profit at TP2 per $100 trade size
dollar_tp3      float — profit at TP3 per $100 trade size
dollar_tp4      float — profit at TP4 per $100 trade size

reasoning       string — 2-3 sentence plain-English explanation of why
                  the signal was generated (from Claude prompt output)
invalidation    string — one sentence describing what would invalidate the signal
strategy        string — which signal service generated this (e.g. "Momentum Crossover")
```

### 19.2 TP & SL calculation rules (used in both rule-based and Claude-prompt paths)

**Stop Loss** (widened from 1.5× to 2.0× ATR after early shadow-mode testing —
1.5× was getting wicked out by routine noise before setups could resolve)
- BUY signals: SL = min(recent swing low − small buffer, entry − (2.0 × ATR on the signal timeframe))
- SELL signals: SL = max(recent swing high + small buffer, entry + (2.0 × ATR))
  — the wider of the swing/ATR stop is used (placed beyond noise); a ~0.15%
  buffer beyond the swing keeps an exact-wick touch from stopping the trade out

**Take Profit levels**
- TP1 = entry ± (1.0 × risk distance)   [conservative, 1:1]
- TP2 = entry ± (2.0 × risk distance)   [standard,     1:2]
- TP3 = entry ± (3.0 × risk distance)   [extended,     1:3]
- TP4 = entry ± (4.5 × risk distance)   [runner,       ~1:4.5 or next major S/R]

Where `risk distance = abs(entry_price - stop_loss)`.

**Dollar figures**
Computed assuming a fixed $100 notional position regardless of
what the user actually trades — makes the card scannable across coins
without needing an account size input:
- dollar_risk = (risk_pct / 100) × 100
- dollar_tp1  = (reward_tp1_pct / 100) × 100
(and so on for TP2–4)

These are illustrative scaling figures, not financial advice. Include
that disclaimer on the card UI.

### 19.3 Dashboard Signal Card UI (what the user sees)

Each card should show:
- Coin name + logo, direction badge (BUY green / SELL red), timeframe
- Confidence percentage with a visual bar
- Entry | SL | TP1 | TP2 | TP3 | TP4 (price levels, 2 dp)
- Risk/Reward column: "Lose $X → Make $Y at TP2" style phrasing
  (per $100 assumed trade size, labelled clearly)
- Reasoning text (collapsible if long)
- Timestamp + strategy name
- Disclaimer line: "Informational only. Not financial advice."

## 20. Claude AI Prompt for Signal Generation

This prompt is designed to be called from a Celery task once per
configured interval per symbol per strategy. It receives computed
indicator values as context and returns a structured JSON signal.

### 20.1 How it fits the backend

```
Celery Beat → scheduled task per symbol/strategy
    → fetch latest N candles from Hyperliquid REST
    → compute all indicator values server-side (Python: ta-lib or pandas_ta)
    → call Claude API with the prompt below
    → parse JSON response
    → if confidence >= 65: write Signal row to DB
    → serve via /api/me/signals/feed/
```

Server-side indicator computation (Python) is needed here even though
chart indicators (Section 10) are computed client-side — the signal
engine runs on a schedule with no browser involved. Use `pandas_ta`
(no native dependency issues) rather than TA-Lib (requires C build).

### 20.2 The Claude prompt (copy into your Celery task as a template)

Use model: `claude-sonnet-4-6`, max_tokens: 1024, temperature: 0.

--- SYSTEM PROMPT ---
You are a professional quantitative crypto trading analyst. You
analyze technical indicator data and produce structured trading
signals. You respond ONLY with a valid JSON object — no preamble,
no explanation outside the JSON, no markdown code fences. If the
data does not present a clear high-confidence setup, you set
confidence_pct below 65 and direction to "NEUTRAL".
--- END SYSTEM PROMPT ---

--- USER PROMPT TEMPLATE ---
Analyze the following technical data for {SYMBOL} on the {TIMEFRAME}
timeframe and generate a trading signal using the {STRATEGY_NAME}
strategy.

CURRENT MARKET DATA:
- Symbol: {SYMBOL}
- Timeframe: {TIMEFRAME}
- Current price (close): {CLOSE}
- 24h high: {HIGH_24H}
- 24h low: {LOW_24H}
- Volume (current candle): {VOLUME}
- Volume MA (20): {VOLUME_MA20}

INDICATOR VALUES (most recent completed candle):
- EMA 9:  {EMA9}
- EMA 21: {EMA21}
- EMA 50: {EMA50}
- RSI (14): {RSI}
- MACD line: {MACD_LINE}
- MACD signal: {MACD_SIGNAL}
- MACD histogram: {MACD_HIST}
- Bollinger Upper: {BB_UPPER}
- Bollinger Middle: {BB_MID}
- Bollinger Lower: {BB_LOWER}
- ATR (14): {ATR}
- Stochastic %K: {STOCH_K}
- Stochastic %D: {STOCH_D}
- VWAP: {VWAP}

RECENT SWING LEVELS (last 50 candles):
- Nearest swing high: {SWING_HIGH}
- Nearest swing low: {SWING_LOW}

STRATEGY FOCUS: {STRATEGY_DESCRIPTION}
(e.g. for Momentum Crossover: "Look for EMA 9 crossing above/below
EMA 21 confirmed by RSI direction and MACD histogram expanding in
the same direction.")

TASK:
1. Evaluate whether the data presents a clear {STRATEGY_NAME} signal.
2. Determine direction: BUY, SELL, or NEUTRAL.
3. Assign a confidence percentage (0–100) based on how many
   conditions align strongly. Only call a signal if >= 65.
4. Calculate entry, stop loss, and TP1–TP4 using these rules:
   - Entry = current close price ({CLOSE})
   - BUY stop loss  = min(swing_low, entry - 1.5×ATR)
   - SELL stop loss = max(swing_high, entry + 1.5×ATR)
   - risk_distance  = abs(entry - stop_loss)
   - TP1 = entry ± 1.0 × risk_distance
   - TP2 = entry ± 2.0 × risk_distance
   - TP3 = entry ± 3.0 × risk_distance
   - TP4 = entry ± 4.5 × risk_distance
   (use + for BUY, - for SELL)
5. Calculate risk_pct, reward_tp1_pct through reward_tp4_pct, and
   risk_reward ratios (reward / risk).
6. Write a 2-3 sentence plain-English reasoning string.
7. Write one sentence describing what would invalidate this signal.

Respond with ONLY this JSON (no other text):
{
  "symbol": "{SYMBOL}",
  "direction": "BUY" | "SELL" | "NEUTRAL",
  "confidence_pct": <integer 0-100>,
  "timeframe": "{TIMEFRAME}",
  "strategy": "{STRATEGY_NAME}",
  "entry_price": <float>,
  "stop_loss": <float>,
  "tp1": <float>,
  "tp2": <float>,
  "tp3": <float>,
  "tp4": <float>,
  "risk_pct": <float>,
  "reward_tp1_pct": <float>,
  "reward_tp2_pct": <float>,
  "reward_tp3_pct": <float>,
  "reward_tp4_pct": <float>,
  "risk_reward_tp1": <float>,
  "risk_reward_tp2": <float>,
  "risk_reward_tp3": <float>,
  "risk_reward_tp4": <float>,
  "dollar_risk": <float>,
  "dollar_tp1": <float>,
  "dollar_tp2": <float>,
  "dollar_tp3": <float>,
  "dollar_tp4": <float>,
  "reasoning": "<2-3 sentences>",
  "invalidation": "<1 sentence>"
}
--- END USER PROMPT TEMPLATE ---

### 20.3 Python helper to fill the prompt

```python
def build_signal_prompt(symbol, timeframe, strategy_name,
                         strategy_description, indicators: dict) -> str:
    template = USER_PROMPT_TEMPLATE  # store the template above as a constant
    replacements = {
        "{SYMBOL}": symbol,
        "{TIMEFRAME}": timeframe,
        "{STRATEGY_NAME}": strategy_name,
        "{STRATEGY_DESCRIPTION}": strategy_description,
        "{CLOSE}":         str(indicators["close"]),
        "{HIGH_24H}":      str(indicators["high_24h"]),
        "{LOW_24H}":       str(indicators["low_24h"]),
        "{VOLUME}":        str(indicators["volume"]),
        "{VOLUME_MA20}":   str(indicators["volume_ma20"]),
        "{EMA9}":          str(indicators["ema9"]),
        "{EMA21}":         str(indicators["ema21"]),
        "{EMA50}":         str(indicators["ema50"]),
        "{RSI}":           str(indicators["rsi"]),
        "{MACD_LINE}":     str(indicators["macd_line"]),
        "{MACD_SIGNAL}":   str(indicators["macd_signal"]),
        "{MACD_HIST}":     str(indicators["macd_hist"]),
        "{BB_UPPER}":      str(indicators["bb_upper"]),
        "{BB_MID}":        str(indicators["bb_mid"]),
        "{BB_LOWER}":      str(indicators["bb_lower"]),
        "{ATR}":           str(indicators["atr"]),
        "{STOCH_K}":       str(indicators["stoch_k"]),
        "{STOCH_D}":       str(indicators["stoch_d"]),
        "{VWAP}":          str(indicators["vwap"]),
        "{SWING_HIGH}":    str(indicators["swing_high"]),
        "{SWING_LOW}":     str(indicators["swing_low"]),
    }
    for k, v in replacements.items():
        template = template.replace(k, v)
    return template
```

### 20.4 Response parsing and safety

```python
import json
import anthropic

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

def generate_signal(symbol, timeframe, strategy_name,
                    strategy_description, indicators):
    prompt = build_signal_prompt(
        symbol, timeframe, strategy_name,
        strategy_description, indicators
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0,
        system=(
            "You are a professional quantitative crypto trading analyst. "
            "You analyze technical indicator data and produce structured "
            "trading signals. You respond ONLY with a valid JSON object — "
            "no preamble, no explanation outside the JSON, no markdown "
            "code fences. If the data does not present a clear "
            "high-confidence setup, set confidence_pct below 65 and "
            "direction to NEUTRAL."
        ),
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    try:
        signal = json.loads(raw)
    except json.JSONDecodeError:
        # Strip accidental markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        signal = json.loads(raw)

    # Only persist if confidence meets threshold
    if signal.get("confidence_pct", 0) >= 65 and \
       signal.get("direction") != "NEUTRAL":
        return signal
    return None
```

### 20.5 Important caveats for this Claude-powered approach

- Claude at temperature=0 is deterministic and consistent, which is
  what you want for signal generation — not creative output.
- The prompt passes pre-computed indicator numbers, not raw candles
  or open-ended "should I buy?" questions. This keeps Claude in the
  role of structured analyst/formatter, not pure oracle — which makes
  outputs more reliable and auditable.
- Even so: Claude is reasoning over indicator snapshots, not running
  a statistical backtest. Before labelling signals "high percentage"
  or "accurate", run the Celery task in shadow mode (generating but
  not surfacing signals) for at least 2-4 weeks and track entry vs
  subsequent price movement. Do not make accuracy claims before this.
- The $100 position reference in the dollar fields is illustrative.
  Make this explicit on every signal card — users trade different
  sizes and some will mistake the figures for a recommended trade size.
- ANTHROPIC_API_KEY must be added to environment variables (Section 15).

## 21. Updated Environment Variables

Same as Section 15, plus the LLM key for signal generation. Keep real
values only in the untracked `backend/.env` (see `backend/.env.example`).
NEVER paste live secrets into this file — it is committed to git.

```
DJANGO_SECRET_KEY=<generate-a-long-random-string>
DATABASE_URL=
REDIS_URL=
HYPERLIQUID_WS_URL=wss://api.hyperliquid.xyz/ws
DODO_PAYMENTS_API_KEY=
DODO_PAYMENTS_WEBHOOK_SECRET=
FRONTEND_URL=
OPENAI_API_KEY=<your-openai-api-key>
```