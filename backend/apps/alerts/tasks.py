"""Price-alert checking (Section 12).

A cheap periodic task: resolve each active alert's current price and trigger any
whose condition is met. No LLM. Crypto prices come from one Hyperliquid allMids
call (regardless of alert count); forex prices come from Yahoo, one request per
pair that actually has an alert (only the handful of majors), so it stays light.
"""

import logging

import requests
from celery import shared_task
from django.utils import timezone

from apps.market_data.client import fetch_all_mids
from apps.market_data.forex import fetch_forex_price

from .models import PriceAlert

logger = logging.getLogger("alerts.tasks")


def run_alert_check() -> dict:
    active = list(PriceAlert.objects.filter(is_active=True).select_related("symbol"))
    if not active:
        return {"checked": 0, "triggered": 0}

    has_crypto = any(not a.symbol.is_forex for a in active)
    has_forex = any(a.symbol.is_forex for a in active)

    # Crypto: one allMids call covers every crypto alert.
    mids = {}
    if has_crypto:
        try:
            mids = fetch_all_mids()
        except requests.RequestException:
            logger.warning("allMids fetch failed")

    # Forex: one Yahoo request per distinct pair that has an alert.
    forex_prices = {}
    if has_forex:
        for feed in {a.symbol.feed_symbol for a in active if a.symbol.is_forex and a.symbol.feed_symbol}:
            try:
                price = fetch_forex_price(feed)
                if price is not None:
                    forex_prices[feed] = price
            except requests.RequestException:
                logger.warning("forex price fetch failed: %s", feed)

    now = timezone.now()
    triggered = 0
    for alert in active:
        if alert.symbol.is_forex:
            price = forex_prices.get(alert.symbol.feed_symbol)
        else:
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
