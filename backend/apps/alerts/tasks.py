"""Price-alert checking (Section 12).

A cheap periodic task: fetch all Hyperliquid mid prices in one call and trigger
any active alert whose condition is met. No LLM, one HTTP request regardless of
how many alerts exist — so it's safe to run continuously.
"""

import logging

import requests
from celery import shared_task
from django.utils import timezone

from apps.market_data.client import fetch_all_mids

from .models import PriceAlert

logger = logging.getLogger("alerts.tasks")


def run_alert_check() -> dict:
    active = list(PriceAlert.objects.filter(is_active=True).select_related("symbol"))
    if not active:
        return {"checked": 0, "triggered": 0}

    try:
        mids = fetch_all_mids()
    except requests.RequestException:
        logger.warning("allMids fetch failed")
        return {"checked": 0, "triggered": 0, "error": "price feed unavailable"}

    now = timezone.now()
    triggered = 0
    for alert in active:
        price = mids.get(alert.symbol.hl_coin)
        if price is None:
            continue
        hit = (
            (alert.condition == PriceAlert.Condition.ABOVE and price >= alert.target_price)
            or (alert.condition == PriceAlert.Condition.BELOW and price <= alert.target_price)
        )
        if hit:
            alert.is_active = False
            alert.triggered_at = now
            alert.triggered_price = price
            alert.seen = False
            alert.save(update_fields=["is_active", "triggered_at", "triggered_price", "seen"])
            triggered += 1

    summary = {"checked": len(active), "triggered": triggered}
    if triggered:
        logger.info("price alerts: %s", summary)
    return summary


@shared_task(name="apps.alerts.tasks.check_price_alerts")
def check_price_alerts():
    return run_alert_check()
