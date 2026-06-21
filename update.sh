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
say "$c_info" "▶ Building images and starting the stack…"
docker compose up -d --build

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
say "$c_info" "▶ Seeding signal strategies…"
docker compose exec -T web python manage.py seed_signal_services

# 6. Done -------------------------------------------------------------------
say "$c_ok" "✓ PulseCharts is up to date and running."
docker compose ps
say "$c_info" "First deploy only — create an admin login:"
say "$c_info" "  docker compose exec web python manage.py createsuperuser"
