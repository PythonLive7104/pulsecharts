#!/usr/bin/env bash
# One-command deploy / update for PulseCharts.
#
#   bash update.sh
#
# Rebuilds the images, (re)creates the stack, applies migrations, and refreshes
# the Hyperliquid symbol list + signal strategies. Idempotent — safe to run on
# every deploy. Run it on the server (where Docker + backend/.env live).
set -euo pipefail

cd "$(dirname "$0")"

c_ok=$'\033[32m'; c_info=$'\033[36m'; c_warn=$'\033[33m'; c_off=$'\033[0m'
say() { printf '%s%s%s\n' "$1" "$2" "$c_off"; }

# 0. Sanity checks ----------------------------------------------------------
command -v docker >/dev/null 2>&1 || { say "$c_warn" "✗ Docker is not installed."; exit 1; }
docker compose version >/dev/null 2>&1 || { say "$c_warn" "✗ 'docker compose' is unavailable."; exit 1; }
[ -f backend/.env ] || { say "$c_warn" "✗ backend/.env is missing — add it before deploying."; exit 1; }

# 1. Pull latest code (only if this is a git checkout) ----------------------
if [ -d .git ]; then
  say "$c_info" "▶ Pulling latest code…"
  git pull --ff-only || say "$c_warn" "  git pull skipped/failed — continuing with local code."
fi

# 2. Build images and (re)create containers ---------------------------------
# --remove-orphans matters: deleting a service from docker-compose.yml does NOT stop
# its running container. When `beat` was folded into the worker (-B), a lingering beat
# container would mean TWO schedulers — every task firing twice (duplicate signals,
# duplicate Telegram pushes, duplicate auto-trades), with nothing obviously broken.
say "$c_info" "▶ Building images and starting the stack…"
docker compose up -d --build --remove-orphans

# 3. Wait for the backend to be ready (DB reachable) ------------------------
say "$c_info" "▶ Waiting for the backend…"
ready=0
for _ in $(seq 1 30); do
  if docker compose exec -T web python manage.py showmigrations >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
[ "$ready" = "1" ] || say "$c_warn" "  Backend slow to start — check 'docker compose logs web'."

# 4. Migrations (web also runs these on start; harmless to repeat) ----------
say "$c_info" "▶ Applying database migrations…"
docker compose exec -T web python manage.py migrate --noinput

# Sync the admin referral code from .env (ADMIN_REFERRAL_CODE).
docker compose exec -T web python manage.py sync_referral_code || true

# 5. Keep symbols + strategies current (idempotent upserts) -----------------
say "$c_info" "▶ Syncing Hyperliquid symbols…"
docker compose exec -T web python manage.py sync_symbols \
  || say "$c_warn" "  sync_symbols failed (network?) — continuing."
say "$c_info" "▶ Seeding forex pairs…"
docker compose exec -T web python manage.py seed_forex \
  || say "$c_warn" "  seed_forex failed — continuing."
say "$c_info" "▶ Seeding signal strategies…"
docker compose exec -T web python manage.py seed_signal_services

# 5b. Register the Telegram webhook (idempotent). Without this Telegram has no
# URL to deliver /start to, so "Connect Telegram" silently does nothing. Skips
# cleanly when TELEGRAM_BOT_TOKEN isn't configured.
say "$c_info" "▶ Registering Telegram webhook…"
docker compose exec -T web python manage.py set_telegram_webhook \
  || say "$c_warn" "  Telegram webhook not set (token unconfigured?) — continuing."

# 6. Reclaim disk ------------------------------------------------------------
# Every --build leaves the superseded image layers dangling and adds a build-cache
# entry, and nothing here ever cleaned them up: on a 40GB VPS that is what eventually
# fills the disk — mid-build, so the deploy fails with "no space left on device"
# rather than anything pointing at the cause. Runs AFTER the stack is up so a prune
# can never race the build. `image prune` only removes dangling (untagged) images,
# never one a container is using. The cache is trimmed to 2GB, not emptied, so the
# next rebuild still hits warm layers.
# Flag note: buildx >= 0.17 renamed --keep-storage to --reserved-space (the old name
# still works but warns on every deploy). Try the new one, fall back for older Docker.
say "$c_info" "▶ Reclaiming disk (dangling images + build cache)…"
docker image prune -f || say "$c_warn" "  image prune skipped."
docker builder prune -f --reserved-space 2g 2>/dev/null \
  || docker builder prune -f --keep-storage 2g \
  || say "$c_warn" "  build-cache prune skipped."

# 7. Done -------------------------------------------------------------------
say "$c_ok" "✓ PulseCharts is up to date and running."
docker compose ps
say "$c_info" "First deploy only — create an admin login:"
say "$c_info" "  docker compose exec web python manage.py createsuperuser"
