#!/usr/bin/env bash
#
# PulseCharts dev runner — launches the stack together and shuts it all down
# cleanly on Ctrl-C:
#   1. Django ASGI server  (API + WebSocket)        :8000
#   2. Hyperliquid relay   (LIVE candle feed)
#   3. Vite frontend       (React app)              :5173
#   4. (optional) Celery worker + beat — the signal engine
#
# Usage:
#   ./dev.sh              # app + Celery (price alerts + signal-outcome eval run;
#                         #   the PAID signal scan stays OFF — no OpenAI spend).
#   SIGNALS=1 ./dev.sh    # also enable the paid LLM signal scan (spends credits).
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv/bin"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# Dev-friendly Django settings (better errors; relaxed WS origin checks).
export DJANGO_DEBUG=true
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

# --- colored log prefixes ---
c_be=$'\033[36m'; c_relay=$'\033[33m'; c_fe=$'\033[32m'; c_sig=$'\033[35m'; c_err=$'\033[31m'; c_off=$'\033[0m'
say() { printf '%s%s%s\n' "$1" "$2" "$c_off"; }

# --- preflight: the things that have broken before ---
preflight_ok=true

if ! "$VENV/python" --version >/dev/null 2>&1; then
  say "$c_err" "✗ venv not found at $VENV — create it: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
  preflight_ok=false
fi

if ! redis-cli ping >/dev/null 2>&1; then
  say "$c_err" "✗ Redis is not responding (needed for Channels + relay). Start it, e.g.: sudo service redis-server start"
  preflight_ok=false
fi

port_busy() { (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":$1 "; }
for port in 8000 5173; do
  if port_busy "$port"; then
    say "$c_err" "✗ Port $port is already in use — stop the stale process first:  fuser -k ${port}/tcp"
    preflight_ok=false
  fi
done

if [ "$preflight_ok" != true ]; then
  say "$c_err" "Preflight failed — fix the above and re-run ./dev.sh"
  exit 1
fi

# --- database: start (and reuse) the dockerized Postgres so dev uses the same
#     DB engine as production. backend/.env's DATABASE_URL points at localhost:5432.
if command -v docker >/dev/null 2>&1; then
  say "$c_be" "▶ Starting Postgres (docker compose 'db' service)…"
  docker compose -f "$ROOT/docker-compose.yml" up -d db >/dev/null 2>&1 \
    || say "$c_err" "  Couldn't start the db container — is Docker running?"
  for _ in $(seq 1 30); do
    docker compose -f "$ROOT/docker-compose.yml" exec -T db pg_isready -U pulsecharts >/dev/null 2>&1 && break
    sleep 1
  done
else
  say "$c_err" "✗ Docker not found — install Docker, or point DATABASE_URL at another Postgres."
  exit 1
fi

say "$c_be" "▶ Applying migrations…"
( cd "$BACKEND" && "$VENV/python" manage.py migrate --noinput ) \
  || { say "$c_err" "migrate failed — see above"; exit 1; }

# Sync the admin referral code from .env (ADMIN_REFERRAL_CODE).
( cd "$BACKEND" && "$VENV/python" manage.py sync_referral_code ) || true

# --- shut everything down on exit (Ctrl-C kills the whole process group) ---
_cleaned=false
cleanup() {
  $_cleaned && return
  _cleaned=true
  echo
  say "$c_err" "Shutting down…"
  # Kill every child in this process group.
  trap '' INT TERM
  kill 0 2>/dev/null
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

# Celery always runs (price alerts + outcome eval are cheap). The PAID signal
# scan only fires when SIGNALS=1 (sets SIGNAL_ENGINE_ENABLED for the worker).
if [ "${SIGNALS:-0}" = "1" ]; then
  export SIGNAL_ENGINE_ENABLED=1
  sig_note="signal scan ON ⚠ spends OpenAI credits"
else
  sig_note="signal scan OFF (set SIGNALS=1 to enable)"
fi
say "$c_be" "▶ Starting PulseCharts (backend :8000 · relay · frontend :5173 · celery · $sig_note). Ctrl-C to stop all."

# 1. Backend ASGI server
( cd "$BACKEND" && "$VENV/python" manage.py runserver 127.0.0.1:8000 2>&1 \
    | sed "s/^/${c_be}[backend]${c_off} /" ) &

# 2. Relay (give the server a moment first)
( sleep 2; cd "$BACKEND" && "$VENV/python" manage.py run_relay 2>&1 \
    | sed "s/^/${c_relay}[relay]  ${c_off} /" ) &

# 3. Frontend
( cd "$FRONTEND" && npm run dev 2>&1 \
    | sed "s/^/${c_fe}[front]  ${c_off} /" ) &

# 4. Celery worker + beat — always on (alerts + outcome eval; scan gated above).
( cd "$BACKEND" && "$VENV/celery" -A config worker -l info 2>&1 \
    | sed "s/^/${c_sig}[worker] ${c_off} /" ) &
( sleep 3; cd "$BACKEND" && "$VENV/celery" -A config beat -l info 2>&1 \
    | sed "s/^/${c_sig}[beat]   ${c_off} /" ) &

wait
