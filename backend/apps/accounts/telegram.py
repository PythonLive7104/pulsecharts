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


def _call(method: str, payload: dict, *, timeout: float = 10.0) -> dict | None:
    if not is_configured():
        return None
    url = API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("telegram %s failed: %s", method, data.get("description"))
            return None
        return data.get("result")
    except (requests.RequestException, ValueError) as exc:
        logger.warning("telegram %s error: %s", method, exc)
        return None


def send_message(chat_id: str, text: str, *, parse_mode: str = "HTML") -> bool:
    """Send a message to a chat. Returns True on success."""
    result = _call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })
    return result is not None


def set_webhook(url: str) -> bool:
    """Point the bot at our webhook URL (idempotent)."""
    return _call("setWebhook", {"url": url, "allowed_updates": ["message"]}) is not None


def delete_webhook() -> bool:
    return _call("deleteWebhook", {}) is not None


def deep_link(token: str) -> str:
    """The t.me deep link that starts the bot with a link token payload."""
    return f"https://t.me/{settings.TELEGRAM_USERNAME}?start={token}"
