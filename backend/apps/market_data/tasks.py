"""Background recorders for market_data.

Currently one job: accumulate the positioning history that Hyperliquid does not
serve retrospectively. See MarketContext for why this exists months before anything
reads it.
"""

from __future__ import annotations

import logging

import requests
from celery import shared_task
from django.utils import timezone

from .client import fetch_asset_contexts
from .models import MarketContext, Symbol

logger = logging.getLogger("market_data.tasks")


def _f(value):
    """Hyperliquid returns numbers as strings; None for anything unparseable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@shared_task(name="apps.market_data.tasks.record_market_context")
def record_market_context() -> dict:
    """Snapshot open interest + funding for every active crypto symbol.

    One API call covers the whole universe, so this is a single request regardless of
    how many coins are tracked.

    Bucketed to the START of the current UTC hour and written with update_or_create
    on (symbol, bucket): running more often than hourly simply refreshes the current
    bucket rather than creating duplicates, so the beat interval can be tightened for
    resolution or loosened for cost without changing the shape of the data.
    """
    try:
        ctxs = fetch_asset_contexts()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("market context fetch failed: %s", exc)
        return {"recorded": 0, "error": str(exc)}
    if not ctxs:
        logger.warning("market context: empty payload — skipping this hour")
        return {"recorded": 0}

    bucket = timezone.now().replace(minute=0, second=0, microsecond=0)
    symbols = Symbol.objects.filter(
        is_active=True, asset_class=Symbol.AssetClass.CRYPTO
    ).exclude(hl_coin="").only("id", "hl_coin")

    recorded = skipped = 0
    for sym in symbols:
        ctx = ctxs.get(sym.hl_coin)
        if not ctx:
            skipped += 1
            continue
        MarketContext.objects.update_or_create(
            symbol=sym, bucket=bucket,
            defaults={
                "open_interest": _f(ctx.get("openInterest")),
                "funding": _f(ctx.get("funding")),
                "mark_px": _f(ctx.get("markPx")),
                "day_ntl_vlm": _f(ctx.get("dayNtlVlm")),
            },
        )
        recorded += 1

    summary = {"recorded": recorded, "skipped": skipped, "bucket": bucket.isoformat()}
    logger.info("market context: recorded=%(recorded)d skipped=%(skipped)d", summary)
    return summary
