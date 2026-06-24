# Deploying PulseCharts (Docker)

The whole stack runs from `docker-compose.yml`: an nginx frontend, the Django
ASGI server (daphne), a Celery worker, Celery beat, the Hyperliquid relay, plus
**Postgres and Redis as containers on the same host**. The database lives next to
the app — no external DB, no cross-region latency. Data persists in the `db-data`
Docker volume across restarts and redeploys.

> 💾 **Backups are now your job.** A self-hosted DB has no managed backups. Set up
> a periodic dump, e.g. a cron running:
> `docker compose exec -T db pg_dump -U pulsecharts pulsecharts | gzip > backup-$(date +%F).sql.gz`

## 1. Configure `backend/.env`

Set these for production (the rest of your signal/OpenAI/Telegram vars stay as-is):

```ini
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<long random string>
DJANGO_ALLOWED_HOSTS=your-domain.com,your.server.ip
FRONTEND_URL=https://your-domain.com
# DATABASE_URL is NOT needed — compose points the app at the in-cluster Postgres.
# REDIS_URL is overridden to the in-cluster redis by compose — leave it.

# Signal engine (already configured for the test)
OPENAI_API_KEY=<your OpenAI key>
OPENAI_MODEL=gpt-4o
SIGNAL_ENGINE_ENABLED=True
SIGNAL_ENGINE_MODE=hybrid          # rules generate, LLM annotates

# Telegram signal delivery (premium). Bot from @BotFather.
TELEGRAM_BOT_TOKEN=<bot token>
TELEGRAM_USERNAME=<bot @username, no '@'>
# TELEGRAM_WEBHOOK_SECRET is auto-derived from the token if you leave it unset.
```

`DJANGO_ALLOWED_HOSTS` **must** include your public domain/IP, or both the API
and the WebSocket (origin-validated) will reject requests.

`FRONTEND_URL` **must** be your real domain — it's used to build referral share
links (`FRONTEND_URL/signup?ref=CODE`) and password-reset links. If left as
localhost, those links won't work for users.

### Database password (compose-level, not `backend/.env`)

The Postgres password is read by Docker Compose itself, so set it in the shell or
a **`.env` file next to `docker-compose.yml`** (the repo root), not `backend/.env`:

```ini
# .env  (repo root)
POSTGRES_PASSWORD=<a long random value>
# POSTGRES_USER / POSTGRES_DB default to "pulsecharts" — override here if you like
```

It defaults to `pulsecharts` if unset — fine for a quick test, but set a strong
one for anything real.

## 2. Build & start

```bash
docker compose up -d --build
```

This starts six services; only the frontend is exposed (port **80**). It proxies
`/api`, `/ws`, `/admin`, and `/static` to the backend internally. The `web`
service runs database migrations automatically on startup.

## 3. One-time post-deploy steps

```bash
# Populate the full Hyperliquid symbol list (fast now — server is near the DB)
docker compose exec web python manage.py sync_symbols

# Seed the 7 major forex pairs (only if OANDA_API_KEY is set — see backend/.env)
docker compose exec web python manage.py seed_forex

# Seed the signal strategies (the 10 services)
docker compose exec web python manage.py seed_signal_services

# Create an admin login for /admin
docker compose exec web python manage.py createsuperuser

# Register the Telegram webhook so premium users can link their account
# (needs your public HTTPS domain — Telegram won't deliver to http/localhost)
docker compose exec web python manage.py set_telegram_webhook https://your-domain.com
```

## 3a. Telegram & referrals

**Telegram (premium signal delivery):**
- Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_USERNAME` (from @BotFather), then run
  `set_telegram_webhook` above. Re-run it whenever the domain changes.
- Premium users connect from **Signals → Connect Telegram** (one tap → press
  Start). Delivery is premium-only and expiry-aware: it stops automatically when
  a subscription lapses and resumes on resubscribe (no re-linking). A Celery beat
  task (`push_telegram_signals`, every 2 min) does the pushing.

**Referrals:**
- Every user gets a personal code (auto-generated) under **Account → Plan &
  Billing → Refer & earn**; they earn $1 per signup and redeem at $9 (Starter) /
  $19 (Pro). Ordinary codes are earn-only (new user stays Free).
- To make a code *grant* a new user 30-day Starter (e.g. a launch promo or a
  partner code), set **`grants_signup_plan = True`** on it in the Django admin
  (Referral codes). The owner code `MAILIONDEV_7788` is already set this way.

## 4. Operate

```bash
docker compose ps                 # service status
docker compose logs -f worker     # watch signal generation
docker compose logs -f relay      # watch the Hyperliquid feed
docker compose restart web        # after an .env change
docker compose down               # stop everything (Redis data persists)
```

## TLS / HTTPS

This compose serves plain HTTP on port 80. For a public deployment put a TLS
terminator in front (Caddy, Traefik, or an nginx with certbot / your host's load
balancer) and point `FRONTEND_URL` at the `https://` URL. WebSockets then run
over `wss://` automatically (the client uses the page's scheme).

## Notes

- Signals are **visible** (not shadow mode) and the engine is **on** — the worker
  scans every 30 min on the 1h/4h timeframes. Add coins to your watchlist for
  more signal volume.
- Re-run `sync_symbols` occasionally (or on each deploy) to keep coverage current;
  add `--prune` to deactivate coins Hyperliquid delists.
