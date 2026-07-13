"""
Django settings for PulseCharts (config project).

Section references point at CLAUDE.md (project spec). Values are read from the
environment so the same settings module serves local dev and production.

Local dev: if DATABASE_URL is unset we fall back to SQLite so the scaffold runs
without a Postgres install. Production must set DATABASE_URL to Postgres
(Section 3 — Postgres directly, not Supabase).
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

# Read a .env file at the backend root if present.
environ.Env.read_env(BASE_DIR / ".env")

# --- Core -----------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# --- Applications ---------------------------------------------------------

DJANGO_APPS = [
    "daphne",  # must precede django.contrib.staticfiles for the ASGI runserver
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "channels",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.billing",
    "apps.market_data",
    "apps.watchlists",
    "apps.chart_layouts",
    "apps.signals",  # v2 — trading signals (Section 13, 19, 20)
    "apps.alerts",   # v2 — price alerts (Section 12)
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves Django's own static (admin / DRF browsable API) under daphne — must
    # sit directly after SecurityMiddleware (WhiteNoise docs).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# WSGI for classic sync serving; ASGI is the real entrypoint (Channels).
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database (Section 3, 8) ----------------------------------------------
# Postgres in production via DATABASE_URL; SQLite fallback for local dev so the
# scaffold runs without a Postgres server installed.

# Treat an empty DATABASE_URL (blank line in .env) as unset -> SQLite fallback.
_default_sqlite = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASES = {
    "default": env.db_url_config(env("DATABASE_URL", default="") or _default_sqlite)
}

# --- Channels / Redis (Section 3, 7) --------------------------------------
# The relay broadcasts normalized candles into Redis-backed Channels groups.

# `or` guards against a blank REDIS_URL= line in .env overriding the default.
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0") or "redis://127.0.0.1:6379/0"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

# --- Auth -----------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- DRF + JWT ------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

# JWT lifetimes. SimpleJWT defaults access to 5 min, which silently breaks
# sessions; the SPA also refreshes on 401 (see frontend api.js).
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
}

# --- CORS (Section 15 FRONTEND_URL) ---------------------------------------

FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173") or "http://localhost:5173"
CORS_ALLOWED_ORIGINS = [FRONTEND_URL]

# --- Behind a TLS-terminating reverse proxy (Caddy in front of nginx) ------
# Trust the forwarded proto so request.is_secure() / absolute URLs are HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# CSRF: the Django admin POSTs over HTTPS on the public domain, so that origin
# must be trusted. Derive from FRONTEND_URL plus the HTTPS form of each real
# host in ALLOWED_HOSTS (bare "localhost" has no dot and is skipped). Override
# wholesale with the CSRF_TRUSTED_ORIGINS env var if needed.
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS", default=[]) or []
for _origin in [FRONTEND_URL, *(f"https://{h}" for h in ALLOWED_HOSTS if "." in h)]:
    if _origin and _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

# --- Hyperliquid upstream (Section 6, 15) ---------------------------------

HYPERLIQUID_WS_URL = env(
    "HYPERLIQUID_WS_URL", default="wss://api.hyperliquid.xyz/ws"
)

# --- Forex data (Yahoo Finance) -------------------------------------------
# Forex pairs use Yahoo Finance's public chart API (Hyperliquid is crypto-only).
# It needs no API key/account and works from any region — unlike broker APIs
# (e.g. OANDA) which aren't available everywhere. It's an unofficial endpoint, so
# flip FOREX_ENABLED=False to disable forex entirely if it gets unreliable
# (crypto is unaffected either way).
FOREX_ENABLED = env.bool("FOREX_ENABLED", default=True)
# How often (seconds) the relay polls Yahoo for fresh candles on watched forex
# charts. Forex moves slower intrabar than crypto, so a 15s tail is fine — and
# keeps request volume low against the public endpoint.
FOREX_POLL_INTERVAL = env.float("FOREX_POLL_INTERVAL", default=15.0)

# --- Paystack (payment provider) ------------------------------------------
# One-time payments in USD that grant 30 days of access (see apps/billing/paystack.py).
# Test keys never move real money; flip PAYSTACK_MODE=live once verified.
PAYSTACK_MODE = env("PAYSTACK_MODE", default="test")  # test | live
_PAYSTACK_LIVE = PAYSTACK_MODE.lower() == "live"
# Key names match what's in backend/.env (TEST_/LIVE_ pairs).
PAYSTACK_SECRET_KEY = (
    env("LIVE_SECRET_KEY", default="") if _PAYSTACK_LIVE else env("TEST_SECRET_KEY", default="")
)
PAYSTACK_PUBLIC_KEY = (
    env("LIVE_PUBLIC_KEY", default="") if _PAYSTACK_LIVE else env("TEST_PUBLIC_KEY", default="")
)
PAYSTACK_CURRENCY = env("PAYSTACK_CURRENCY", default="USD")

# --- Admin promo codes ----------------------------------------------------
# Codes the admin shares so specific users can self-upgrade for a fixed window
# (to trial premium) from the Plan & Billing page. The same redeem endpoint
# accepts either code and grants the matching plan. Empty = that code is
# disabled. One redemption per user per code value — rotating a code lets a user
# redeem again (a fresh window). The Starter code never downgrades an active Pro
# user (the redeem endpoint guards against it).
ADMIN_PRO_CODE = env("ADMIN_PRO_CODE", default="")
ADMIN_PRO_DAYS = env.int("ADMIN_PRO_DAYS", default=30)
ADMIN_STARTER_CODE = env("ADMIN_STARTER_CODE", default="")
ADMIN_STARTER_DAYS = env.int("ADMIN_STARTER_DAYS", default=30)

# --- Transactional email (Resend) -----------------------------------------
# Used for password-reset links and payment confirmations. Email is optional:
# with no RESEND_API_KEY the send helpers log and no-op, so local/dev runs work
# without it. The From address must be on a domain verified in the Resend
# dashboard (verify getpulsecharts.com there before going live).
RESEND_API_KEY = env("RESEND_API_KEY", default="")
RESEND_FROM_EMAIL = env(
    "RESEND_FROM_EMAIL", default="PulseCharts <noreply@getpulsecharts.com>"
)
# Where the landing-page "Contact us" form delivers messages.
CONTACT_US_EMAIL = env("CONTACT_US_EMAIL", default="")

# --- Trading signals (Section 13, 19, 20) ---
# Note: Section 20 of CLAUDE.md specs Claude/Anthropic; per the developer's
# choice the signal engine runs on OpenAI instead. Update CLAUDE.md §20–21 to
# match when convenient.

OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", default="")  # optional: Azure/proxy/compatible endpoint
# Default is overridable — set OPENAI_MODEL to the exact model you intend to use.
OPENAI_MODEL = env("OPENAI_MODEL", default="gpt-4o")
SIGNAL_TEMPERATURE = env.float("SIGNAL_TEMPERATURE", default=0.0)  # 0 = deterministic

# --- Telegram signal delivery (premium) -----------------------------------
# Bot created via @BotFather. The push task + webhook activate only when the
# token is set, so the app runs fine without Telegram configured.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_USERNAME = env("TELEGRAM_USERNAME", default="").lstrip("@")  # bot @username, no '@'
# Secret in the webhook URL path so only Telegram can reach it. Defaults to a
# value derived from the bot token if not set explicitly.
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")
if not TELEGRAM_WEBHOOK_SECRET and TELEGRAM_BOT_TOKEN:
    import hashlib
    TELEGRAM_WEBHOOK_SECRET = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]
# How often the push task scans for new signals to send (seconds).
TELEGRAM_PUSH_INTERVAL = env.float("TELEGRAM_PUSH_INTERVAL", default=120.0)

# --- Admin / owner referral code ------------------------------------------
# The special referral code that grants new signups a 30-day Starter plan (and
# credits its owner $1). Change it here in .env; the value is synced to the DB on
# startup by `manage.py sync_referral_code` (run from dev.sh / update.sh / web).
# Empty = no admin grant code.
ADMIN_REFERRAL_CODE = env("ADMIN_REFERRAL_CODE", default="").strip().upper()

# Master switch for the (paid) LLM signal scan. Celery Beat always runs the cheap
# tasks (alert checks, outcome eval); the costly scan only fires when this is on.
SIGNAL_ENGINE_ENABLED = env.bool("SIGNAL_ENGINE_ENABLED", default=False)

# How signals are produced:
#   "llm_gate" — the LLM decides direction + confidence and can veto a setup (more
#                selective; backtested as lower hit-rate on a trending regime).
#   "hybrid"   — the rule-based trigger decides direction (the edge the backtest
#                measures) and ALWAYS generates the signal; the LLM only writes the
#                reasoning/invalidation + a confidence read for the card. Best of
#                both: rule-based hit-rate + volume, plus LLM explanations.
#   "rules"    — fully deterministic, NO LLM call. Rules pick direction +
#                confidence; reasoning is templated. Zero per-signal cost, which is
#                what makes lower-timeframe / high-volume scanning economical.
SIGNAL_ENGINE_MODE = env("SIGNAL_ENGINE_MODE", default="rules")  # "rules" | "hybrid" | "llm_gate"
# Fallback confidence for a hybrid signal when the LLM annotation call fails.
SIGNAL_RULE_CONFIDENCE = env.int("SIGNAL_RULE_CONFIDENCE", default=75)

# Only surface signals at/above this confidence (Section 19.1 / 20).
SIGNAL_MIN_CONFIDENCE = env.int("SIGNAL_MIN_CONFIDENCE", default=65)

# Confluence collapse (delivery-side, Option A). The scan stores one signal per
# (symbol, service, timeframe), so a coin can surface several cards at once — one
# per strategy. Confluence collapses those to a SINGLE signal per (symbol,
# timeframe): the direction the most distinct strategies agree on, surfaced only
# when at least this many concur. The highest-confidence agreeing call is shown,
# annotated with how many strategies agree.
#   1  = collapse only (one card per symbol+timeframe; no agreement required)
#   2+ = require genuine confluence (fewer, higher-conviction signals)
# Applies to the in-app feed and Telegram delivery only — it reads already-stored
# signals and never changes generation, so it's fully reversible via this setting.
SIGNAL_CONFLUENCE_MIN = env.int("SIGNAL_CONFLUENCE_MIN", default=1)

# Rule-based pre-gate: skip the (paid) LLM call when a strategy's basic
# conditions clearly aren't present (apps/signals/pregate.py). Big cost saver.
SIGNAL_PREGATE_ENABLED = env.bool("SIGNAL_PREGATE_ENABLED", default=True)

# Regime filter: only allow trend signals in a genuinely trending market —
# ADX above SIGNAL_ADX_MIN (trend strength) AND the setup agreeing with the
# higher-timeframe trend. Keeps trend strategies out of chop. Runs before the
# LLM call, so it also saves tokens.
SIGNAL_REGIME_FILTER_ENABLED = env.bool("SIGNAL_REGIME_FILTER_ENABLED", default=True)
# 28 (was 25→20): pushed above the standard 25 trend line to keep trades out of
# ranging markets, where both tight and wide stops bleed (the user wants few/no
# range trades). Raise toward 30 to be stricter; lower if volume drops too far.
SIGNAL_ADX_MIN = env.float("SIGNAL_ADX_MIN", default=28.0)

# Chop filter (apps/signals/tasks._regime_ok): minimum EMA9–EMA21 separation as a
# multiple of ATR. In a range the fast EMAs bunch/flatten; demanding a real gap
# rejects those setups. 0 disables. 0.5 = moderately strict; raise for fewer range
# trades, lower if it starves volume. Breakout strategies are exempt (squeeze fires
# with bunched EMAs by design). Can't be backtested — the backtest skips the regime
# filter — so tune it live over the observation window.
SIGNAL_EMA_SEP_MIN_ATR = env.float("SIGNAL_EMA_SEP_MIN_ATR", default=0.5)

# Higher-timeframe agreement (apps/signals/tasks._regime_ok): a 1h setup must also
# agree with the 4h 200-EMA bias (4h -> 1d). This is a SECOND 200 EMA, on another
# timeframe — independent of SIGNAL_EMA200_TREND_FILTER, which governs the 200 EMA
# on the signal's own timeframe. Measured on 450 1h bars it rejects ~52% of setups
# that already cleared ADX + chop, so it is the single largest volume cost in the
# live scan. Off = the 200 EMA still decides trend, just on the entry timeframe
# alone. Only consulted when SIGNAL_EMA200_TREND_FILTER is on. Like the chop filter,
# it can't be backtested (the backtest skips the regime filter) — tune it live.
SIGNAL_HTF_REGIME_ENABLED = env.bool("SIGNAL_HTF_REGIME_ENABLED", default=True)

# EMA-alignment gate every non-breakout signal must pass (apps/signals/pregate.py).
# Switchable without a deploy; defaults to the backtest winner (stack50).
#   stack50   : EMA9 > EMA21 > EMA50   (default — best volume/quality balance)
#   stack200  : EMA9 > EMA21 > EMA200  (strict major-trend stack — fewest signals)
#   filter200 : close > EMA200 and EMA9 > EMA21
SIGNAL_EMA_GATE = env("SIGNAL_EMA_GATE", default="stack50")

# 200-EMA trend filter (apps/signals/pregate.py + tasks._regime_ok). When True
# (historical default / "hard invariant"), non-breakout strategies require price on
# the trend-correct side of the 200 EMA AND the higher timeframe (4h/1d) 200-EMA
# regime must agree. When False, the 200 EMA is dropped from every strategy trigger
# AND the HTF regime check — direction rests on the fast 9/21(/50) EMAs and the
# Fib-pullback zone (guard D) becomes the entry confirmation. Set False together with
# a non-zero SIGNAL_FIB_PULLBACK_MIN so entries stay confirmed by structure rather
# than just chasing the fast EMAs. Backtest with `backtest --no-ema200 --fib`.
SIGNAL_EMA200_TREND_FILTER = env.bool("SIGNAL_EMA200_TREND_FILTER", default=True)

# Overextension guard (A — apps/signals/pregate.py). Blocks NEW non-breakout
# entries once price has stretched more than this many ATRs beyond EMA21 — a
# parabolic blow-off where the trend gates are MAXIMALLY satisfied yet a fresh
# entry is just chasing the top (the runaway-USDJPY case that kept re-issuing
# BUYs). Distance = (close − EMA21) / ATR. 0 disables. Breakout strategies are
# exempt (extension is their premise). ~2.5–3 ATR is "stretched"; lower = stricter.
SIGNAL_OVEREXT_ATR_MULT = env.float("SIGNAL_OVEREXT_ATR_MULT", default=2.5)

# Overbought/oversold cap (B — apps/signals/pregate.py). Rejects NEW non-breakout
# entries at an RSI extreme: no BUY above _OVERBOUGHT, no SELL below _OVERSOLD — i.e.
# don't enter right as momentum is most stretched. This catches the sustained-trend
# chase (e.g. USDJPY BUY at RSI 72 near the high) that the distance guard (A) can't
# see, because once the EMA21 catches up to a staircase climb the ATR-stretch shrinks
# below A's threshold. Trade-off: in a strong trend RSI can sit >70 for many bars, so
# a low cap also skips some valid continuation — raise toward 78–80 to ride trends
# harder, lower toward 70 to refuse all overbought entries. 0 disables a bound;
# breakout strategies are exempt.
SIGNAL_RSI_OVERBOUGHT = env.float("SIGNAL_RSI_OVERBOUGHT", default=70.0)
SIGNAL_RSI_OVERSOLD = env.float("SIGNAL_RSI_OVERSOLD", default=30.0)

# Fib-pullback gate (D — apps/signals/pregate.py). Only allow a NEW non-breakout
# entry once price has retraced into the [MIN, MAX] band of the most recent impulse
# leg (a fractal-pivot swing) — "buy the dip / sell the rally" instead of chasing an
# extended move that then snaps back into the stop. This is the structural version of
# guards A/B: instead of measuring stretch from an EMA or RSI, it requires an actual
# retracement of real swing structure. MIN <= 0 DISABLES the gate (the default — so
# it changes nothing until deliberately enabled). 0.5–0.786 is the classic
# continuation zone; below MIN = too shallow (still chasing), above MAX = retraced far
# enough the swing is likely breaking (reversal, not a pullback). Trade-off: far fewer
# signals, and it SKIPS strong trends that never pull back — validate with
# `backtest --fib` before enabling live. Breakout strategies are exempt.
SIGNAL_FIB_PULLBACK_MIN = env.float("SIGNAL_FIB_PULLBACK_MIN", default=0.0)
SIGNAL_FIB_PULLBACK_MAX = env.float("SIGNAL_FIB_PULLBACK_MAX", default=0.786)

# Re-entry cooldown (C — apps/signals/tasks.py). After a same-direction signal on a
# (symbol, strategy, timeframe), suppress a fresh one in that direction for this
# many BARS even once the prior call has CLOSED. Stops a strong trend re-issuing
# the same BUY on back-to-back scans as each call resolves at TP. 0 disables. A
# genuine trend flip is never blocked (cooldown is keyed per direction).
SIGNAL_REENTRY_COOLDOWN_BARS = env.int("SIGNAL_REENTRY_COOLDOWN_BARS", default=3)

# Stop-loss width as ATR multiples, PER ASSET CLASS. The stop is clamped to
# [floor, cap]×ATR (anchored to the nearest swing pivot in between). Crypto is much
# more volatile than forex, so a 2×ATR stop was getting wicked out on crypto before
# setups resolved — crypto gets a WIDER band. Forex keeps the tighter band (its
# original problem was stops that were too WIDE, now fixed). Wider stop ⇒ larger R
# ⇒ TPs are a bigger % move, so TP2 becomes the realistic target and TP3 fills less
# — an accepted trade-off for fewer premature stop-outs. Tune live via env.
SIGNAL_ATR_STOP_FLOOR = {
    "crypto": env.float("SIGNAL_ATR_FLOOR_CRYPTO", default=3.0),  # was 2.0
    "forex": env.float("SIGNAL_ATR_FLOOR_FOREX", default=2.0),
}
SIGNAL_ATR_STOP_CAP = {
    "crypto": env.float("SIGNAL_ATR_CAP_CRYPTO", default=4.5),  # was 3.0
    "forex": env.float("SIGNAL_ATR_CAP_FOREX", default=3.0),
}

# Optional: your model's price per 1M tokens, so each scan can log estimated $.
# Leave 0 to skip the dollar estimate (token counts are still logged).
OPENAI_PRICE_IN_PER_1M = env.float("OPENAI_PRICE_IN_PER_1M", default=0.0)
OPENAI_PRICE_OUT_PER_1M = env.float("OPENAI_PRICE_OUT_PER_1M", default=0.0)

# Daily quota by plan (Section 13.3). Signals are a PREMIUM feature, so free = 0
# (locked); premium gets a meaningful cap (-1 == unlimited).
SIGNAL_DAILY_QUOTA = {
    "free": env.int("SIGNAL_QUOTA_FREE", default=0),
    "premium": env.int("SIGNAL_QUOTA_PREMIUM", default=50),
}

# Which timeframes the signal engine evaluates (Section 20.1).
# 1h/4h: backtested best expectancy (52% win, +0.63R exit-at-best). A 15m/1h trial
# measurably degraded the edge (45% win, NEGATIVE exit-at-TP1) — lower frames are
# noisier and fees eat the smaller moves — so the engine stays on 1h/4h. The
# compressed 1/1.5/2/3 TP geometry already delivers the TP3/TP4 fills without
# dropping to a lower frame. Avoid 1m/5m entirely.
SIGNAL_TIMEFRAMES = env.list("SIGNAL_TIMEFRAMES", default=["1h", "4h"])

# Skip crypto signal generation during the weekend window (Fri 21:00 → Sun 21:00
# UTC, same window used to close forex). Weekend crypto is thin and choppy and
# backtests far worse — live results show ~35% weekend win-rate vs ~76% on
# weekdays (Saturday worst at ~27%). On by default; set False to scan crypto 24/7.
SIGNAL_SKIP_CRYPTO_WEEKEND = env.bool("SIGNAL_SKIP_CRYPTO_WEEKEND", default=True)

# Cap how many symbols a single scan evaluates. 0 = all active (the default).
#
# The cap existed to control LLM cost, but the engine now runs SIGNAL_ENGINE_MODE=rules
# — no LLM call — so it was guarding a bill that no longer exists while doing real
# damage: it slices the crypto set as watched[:limit] under Symbol.Meta.ordering, i.e.
# the SAME N coins every scan, forever. Every other watched coin was never scanned at
# all. Leave at 0 unless candle-fetch load actually becomes a problem.
SIGNAL_SCAN_SYMBOL_LIMIT = env.int("SIGNAL_SCAN_SYMBOL_LIMIT", default=0)

# Outcome evaluation (Section 13.7, 18): how many candles after generation before an
# unresolved call is closed out. 48 bars = 2 days on 1h, 8 days on 4h.
#
# This is NOT cosmetic. The scan won't issue a new signal while a strategy still has
# an open call on that symbol+timeframe, so an open call holds its slot — and with no
# expiry, calls accumulated forever (712 of them) until almost every slot on the
# scanned coins was blocked, fresh signals dried up, and delivery (which needs
# SIGNAL_CONFLUENCE_MIN fresh strategies agreeing) starved. A 1h setup that has gone
# 2 days without touching its stop or a target is dead regardless of where price sits.
# A call that already banked TP1/TP2 closes at that banked level, not as EXPIRED.
SIGNAL_EVAL_BARS = env.int("SIGNAL_EVAL_BARS", default=48)

# Shadow mode: keep generating + evaluating signals but DON'T surface them in the
# user feed (run for weeks, validate realized accuracy before any claims, 13.7).
SIGNAL_SHADOW_MODE = env.bool("SIGNAL_SHADOW_MODE", default=False)

# Daily housekeeping: how long to keep RESOLVED signals (and their deliveries) and
# already-seen triggered price alerts before purging them, to keep the database
# small. Open (PENDING) calls are never purged regardless of age. NOTE: realized
# accuracy stats and the "Recent results" history only span this window — raise it
# (e.g. 30) if you want a longer accuracy track record, lower it to free more DB.
SIGNAL_RETENTION_DAYS = env.int("SIGNAL_RETENTION_DAYS", default=30)

# --- Celery (Section 3, 13.6) ---

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True

# Recurring signal scan (Section 13.6).
# TESTING: 300s (matches the 5m candle — scanning faster just re-pays for the
# same in-progress candle). Each scan makes (symbols × timeframes × strategies)
# LLM calls, so this cadence + the symbol cap keeps cost modest.
CELERY_BEAT_SCHEDULE = {
    "scan-signals": {
        "task": "apps.signals.tasks.scan_all_signals",
        "schedule": env.float("SIGNAL_SCAN_INTERVAL", default=300.0),
    },
    # Resolve pending signals against later price (cheap — no LLM).
    "evaluate-signals": {
        "task": "apps.signals.tasks.evaluate_pending_signals",
        "schedule": env.float("SIGNAL_EVAL_INTERVAL", default=600.0),
    },
    # Check price alerts against live mid prices (cheap — one HTTP call).
    "check-price-alerts": {
        "task": "apps.alerts.tasks.check_price_alerts",
        "schedule": env.float("ALERT_CHECK_INTERVAL", default=30.0),
    },
    # Push new signals to linked premium users' Telegram (no-op if unconfigured).
    "push-telegram-signals": {
        "task": "apps.signals.tasks.push_telegram_signals",
        "schedule": env.float("TELEGRAM_PUSH_INTERVAL", default=120.0),
    },
    # Daily cleanup: purge resolved signals + seen alerts past the retention
    # window so the database doesn't grow without bound.
    "purge-old-data": {
        "task": "apps.signals.tasks.purge_old_data",
        "schedule": env.float("PURGE_INTERVAL", default=86400.0),  # once a day
    },
    # Trim watchlists / saved layouts back to plan caps for users whose paid plan
    # has lapsed to Free (catches silent expiries with no billing webhook).
    "enforce-plan-limits": {
        "task": "apps.accounts.tasks.enforce_plan_limits",
        "schedule": env.float("PLAN_ENFORCE_INTERVAL", default=86400.0),  # once a day
    },
    # Telegram-notify users whose paid plan just lapsed (signals have stopped —
    # resubscribe to resume). Hourly so the notice lands promptly after expiry.
    "notify-expired-plans": {
        "task": "apps.accounts.tasks.notify_expired_plans",
        "schedule": env.float("EXPIRY_NOTICE_INTERVAL", default=3600.0),  # hourly
    },
}

# --- i18n / static --------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# WhiteNoise: compress static at collectstatic time and serve it from daphne.
# Non-manifest variant so a missing hash entry can never 500 a request.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
