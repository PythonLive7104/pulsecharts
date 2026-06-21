# PulseCharts — Backend

Django 5 + DRF + Channels backend for the PulseCharts crypto charting app.
See [`../CLAUDE.md`](../CLAUDE.md) for the full product spec; section references
in the code point there.

## Stack

- **Django 5 + DRF** — REST API (auth, symbols, watchlist, layouts, billing).
- **Channels + Redis** — browser-facing `/ws/market/` relay (Section 7).
- **Postgres** in production; **SQLite** fallback locally if `DATABASE_URL` is unset.
- **JWT auth** (SimpleJWT) for the React SPA.

## Setup

```bash
# from repo root — venv already created at ../.venv
../.venv/bin/pip install -r requirements.txt
cp .env.example .env            # then edit
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py seed_symbols      # starter symbol set
../.venv/bin/python manage.py createsuperuser   # for /admin
```

## Run

```bash
# ASGI server (HTTP + WebSocket) — Redis must be running
../.venv/bin/python manage.py runserver

# Hyperliquid relay (separate process; needs Redis + network)
../.venv/bin/python manage.py run_relay
```

**Or run everything at once** (recommended) — from the repo root, `./dev.sh`
starts the server, relay, and frontend together with one labeled log stream and
shuts them all down on Ctrl-C. It also preflight-checks Redis and that ports
8000/5173 are free. The **relay must be running** for the chart to update live —
it streams the `1m` interval, so live ticks show on the 1m timeframe.

## API (Section 9)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/auth/register/` | open signup |
| POST | `/api/auth/token/` `…/refresh/` | JWT login |
| GET | `/api/me/` · `/api/me/entitlements/` | profile + unlocked indicators |
| GET | `/api/symbols/` | available symbols |
| GET | `/api/symbols/{ticker}/candles/?interval=1m&limit=500` | historical OHLCV |
| GET/POST | `/api/watchlist/` · DELETE `/api/watchlist/{id}/` | capped per tier |
| GET/POST | `/api/chart-layouts/` · `/api/chart-layouts/{id}/` | premium = multiple |
| POST | `/api/billing/checkout/` · `/api/billing/webhook/` | Dodo Payments |
| WS | `/ws/market/` | `{"action":"subscribe","symbol":"BTC-USD"}` |

## Known TODOs before production

- **Dodo Payments** (`apps/billing/dodo.py`): confirm checkout + webhook
  contract and signature scheme; `BILLING_LIVE` gates real charges until
  merchant onboarding is done (Section 16). Premium checkout returns a clean
  `503 coming-soon` until then.
- ~~**Candle timestamp** (`apps/market_data/normalize.py`): verify `t` vs `T`~~
  ✅ **Resolved 2026-06-18** against the live feed: `t` = open time, `T` = close
  time; numeric fields arrive as strings. Re-verify with
  `scripts/verify_hyperliquid.py` if Hyperliquid changes their contract.
- **Demand-driven upstream subscriptions** (`apps/market_data/relay.py`):
  v1 subscribes to all active symbols; target is per-active-client (Section 7).
- **Symbol coverage**: confirm each seeded ticker is listed on Hyperliquid
  (Section 16).
