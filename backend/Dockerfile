# PulseCharts backend image. One image, four roles (web / worker / beat / relay)
# — the role is chosen by the command in docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# curl is handy for container healthchecks; no compiler needed (wheels only).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Collect static at build (no DB touched) so WhiteNoise can serve the Django
# admin / DRF browsable API assets under daphne.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8000

# Default role: ASGI server (HTTP + WebSocket). Overridden for worker/beat/relay.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
