"""Thin Telegram Bot API client (no SDK — just requests, like the rest of the app).

Used for: linking a user's Telegram via the bot's /start deep link, and pushing
signal messages to premium users. All calls are no-ops (return False) when no bot
token is configured, so the app runs fine without Telegram set up.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger("telegram")

API_BASE = "https://api.telegram.org/bot{token}/{method}"


def is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


# Telegram errors that mean the CHAT is gone, not that the call failed: the user
# blocked the bot, deleted their account, or the chat no longer exists. Retrying these
# is pure waste and never recovers — thirteen blocked users were costing thirteen
# requests on every push cycle, twice a minute, forever, and burying real failures in
# the log while they did it.
_PERMANENT_ERRORS = (
    "bot was blocked by the user",
    "user is deactivated",
    "chat not found",
    "bot can't initiate conversation",
)


def _is_permanent(description: str | None) -> bool:
    text = (description or "").lower()
    return any(reason in text for reason in _PERMANENT_ERRORS)


def _deactivate_chat(chat_id: str, description: str | None) -> None:
    """Turn delivery off for a chat Telegram will never accept again.

    Flips ``telegram_active`` only, leaving ``telegram_chat_id`` in place, so the
    account lands in the ``telegram_can_reconnect`` state: the user can unblock the
    bot and reconnect from the UI without relinking from scratch.
    """
    from django.contrib.auth import get_user_model

    try:
        updated = (get_user_model().objects
                   .filter(telegram_chat_id=str(chat_id), telegram_active=True)
                   .update(telegram_active=False))
    except Exception:  # noqa: BLE001 — delivery must never die on a bookkeeping error
        logger.exception("telegram: could not deactivate chat %s", chat_id)
        return
    if updated:
        logger.warning("telegram: delivery OFF for chat %s (%s) — user may reconnect",
                       chat_id, description)


def _request(method: str, payload: dict, *, timeout: float = 10.0) -> dict | None:
    """Raw Bot API response (including a failed one), or None if the call never
    completed. Lets callers tell a permanent chat error from a transient failure."""
    if not is_configured():
        return None
    url = API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("telegram %s error: %s", method, exc)
        return None


def _call(method: str, payload: dict, *, timeout: float = 10.0) -> dict | None:
    data = _request(method, payload, timeout=timeout)
    if data is None:
        return None
    if not data.get("ok"):
        logger.warning("telegram %s failed: %s", method, data.get("description"))
        return None
    return data.get("result")


def send_message(chat_id: str, text: str, *, parse_mode: str = "HTML") -> bool:
    """Send a message to a chat. Returns True on success.

    A permanent chat error (blocked, deactivated, missing) also switches delivery off
    for that chat, so the next cycle doesn't pay for it again.
    """
    data = _request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })
    if data is None:
        return False
    if data.get("ok"):
        return True
    description = data.get("description")
    if _is_permanent(description):
        _deactivate_chat(chat_id, description)
    else:
        logger.warning("telegram sendMessage failed: %s", description)
    return False


def set_webhook(url: str) -> bool:
    """Point the bot at our webhook URL (idempotent)."""
    return _call("setWebhook", {"url": url, "allowed_updates": ["message"]}) is not None


def delete_webhook() -> bool:
    return _call("deleteWebhook", {}) is not None


def deep_link(token: str) -> str:
    """The t.me deep link that starts the bot with a link token payload."""
    return f"https://t.me/{settings.TELEGRAM_USERNAME}?start={token}"
