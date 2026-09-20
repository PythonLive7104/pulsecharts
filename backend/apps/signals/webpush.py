"""Web Push (VAPID) transport for the in-app signal feed.

Mirrors apps.signals.telegram in shape: a thin, failure-tolerant sender that the
push task drives. Nothing here decides WHICH signals go out — that is the task's
job, using the same confluence/freshness/quota path as every other channel.

Why this exists at all: the in-app feed was PULL-based, delivering only when a user
opened the page, and delivery latency measurably decides whether a signal wins.
Server-initiated push is what lets the feed run the tight freshness window that buys
the accuracy, instead of trading ~3 points away to stay visible.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

logger = logging.getLogger("signals.webpush")

# 404/410 mean the browser threw the subscription away (cleared data, uninstalled).
# Anything else is transient and must NOT deactivate the row — a push-service outage
# would otherwise silently unsubscribe every user at once.
_GONE_STATUSES = (404, 410)


def is_configured() -> bool:
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


def send_push(subscription, payload: dict, *, timeout: float = 10.0) -> tuple[bool, str]:
    """Send one notification. Returns (delivered, reason).

    reason is "" on success, "gone" when the subscription is dead and should be
    deactivated, or a short error string for anything transient.
    """
    if not is_configured():
        return False, "not-configured"
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - dependency missing in a stale image
        logger.warning("pywebpush not installed — web push disabled")
        return False, "no-pywebpush"

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            # The spec requires a contact for the push service to reach if this sender
            # misbehaves; "mailto:" prefix is mandatory.
            vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIM_EMAIL}"},
            timeout=timeout,
        )
        return True, ""
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in _GONE_STATUSES:
            return False, "gone"
        logger.warning("web push failed (%s): %s", status, exc)
        return False, f"http-{status}" if status else "error"
    except Exception as exc:  # noqa: BLE001 - transport must never break the beat loop
        logger.warning("web push error: %s", exc)
        return False, "error"


def format_signal(signal) -> dict:
    """Notification payload for a new signal.

    Deliberately terse: a notification is a PROMPT to open the feed, not the card.
    Entry/stop/targets live in the app, where they are current — a notification can
    sit unread for hours, and a stale price in a system tray is exactly the failure
    this whole channel exists to avoid. No accuracy or performance claim (§13.7).
    """
    sym = signal.symbol.ticker
    return {
        "title": f"{signal.direction} {sym} · {signal.timeframe}",
        "body": f"{signal.service.name} — entry {signal.entry_price:g}. Open to view levels.",
        "tag": f"signal-{signal.pk}",
        "url": "/signals",
    }


def format_update(signal, label: str) -> dict:
    """Notification payload for a trade update (target tagged / closed)."""
    return {
        "title": f"{signal.symbol.ticker} · {label}",
        "body": f"{signal.direction} {signal.timeframe} — open to view the update.",
        "tag": f"update-{signal.pk}",
        "url": "/signals",
    }
