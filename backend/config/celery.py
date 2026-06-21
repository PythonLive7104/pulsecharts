"""Celery app for PulseCharts (Section 3, 13.6).

Drives the v2 signal engine: Celery Beat schedules a recurring scan that
evaluates each active strategy against tracked symbols and writes Signal rows.
Broker + result backend are Redis (same instance as Channels).

Run alongside the app:
    celery -A config worker -l info
    celery -A config beat -l info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("pulsecharts")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
