"""Auto-trade data model (v2 follow-on to the signals feature).

When a signal is generated, Pro users who have connected a broker and enabled
auto-trade get the trade placed on their own exchange account automatically —
a copy-trading engine where the "master" is the signal generator.

Three models:
  - BrokerConnection  — a user's (encrypted) exchange credentials + status
  - AutoTradeConfig   — the per-user risk envelope (sizing, leverage, caps)
  - TradeExecution    — one row per (user, signal) attempt; its unique constraint
                        makes execution idempotent (mirrors signals.TelegramDelivery)

Credentials are never stored in plaintext — see crypto.py. No order is placed
unless settings.AUTO_TRADE_ENABLED is on (global kill switch) AND the user's
config is enabled.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt


class BrokerConnection(models.Model):
    """A user's connection to an exchange. One per user for now (Bybit only)."""

    class Broker(models.TextChoices):
        BYBIT = "bybit", "Bybit"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        ACTIVE = "active", "Active"
        ERROR = "error", "Error"
        REVOKED = "revoked", "Revoked"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broker_connection"
    )
    broker = models.CharField(max_length=16, choices=Broker.choices, default=Broker.BYBIT)
    testnet = models.BooleanField(default=True)  # safe default: testnet until opted to live

    # Fernet-encrypted credentials. Never read these directly — use the
    # set_credentials/get_credentials helpers.
    api_key_enc = models.TextField()
    api_secret_enc = models.TextField()

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # Set True only after we confirm the key can trade AND cannot withdraw.
    permission_verified = models.BooleanField(default=False)
    last_error = models.CharField(max_length=300, blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self.api_key_enc = encrypt(api_key)
        self.api_secret_enc = encrypt(api_secret)

    def get_credentials(self) -> tuple[str, str]:
        return decrypt(self.api_key_enc), decrypt(self.api_secret_enc)

    @property
    def is_usable(self) -> bool:
        return self.status == self.Status.ACTIVE and self.permission_verified

    def __str__(self):
        return f"{self.user_id} · {self.broker} ({self.status})"


class AutoTradeConfig(models.Model):
    """Per-user risk envelope. Strategy/symbol scope is NOT here — it reuses the
    user's existing signal subscriptions and watchlist."""

    class Sizing(models.TextChoices):
        RISK_PCT = "risk_pct", "Risk % of balance"
        FIXED_USD = "fixed_usd", "Fixed USD notional"
        PCT_BALANCE = "pct_balance", "% of balance notional"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="auto_trade_config"
    )
    # Master switch (per-user kill switch). The global settings.AUTO_TRADE_ENABLED
    # gates everyone on top of this.
    enabled = models.BooleanField(default=False)

    sizing_mode = models.CharField(max_length=16, choices=Sizing.choices, default=Sizing.RISK_PCT)
    risk_pct = models.FloatField(default=1.0)       # used when sizing_mode = risk_pct
    fixed_usd = models.FloatField(default=100.0)    # used when sizing_mode = fixed_usd
    pct_balance = models.FloatField(default=5.0)    # used when sizing_mode = pct_balance

    leverage = models.PositiveSmallIntegerField(default=3)
    max_open_positions = models.PositiveSmallIntegerField(default=5)
    max_daily_trades = models.PositiveSmallIntegerField(default=10)

    # How to scale out across the four take-profits (must sum to 100).
    tp_distribution = models.JSONField(default=list)  # e.g. [25, 25, 25, 25]
    # After this TP is hit, move stop to break-even. 0 = never. (1 => after TP1.)
    move_sl_to_be_after_tp = models.PositiveSmallIntegerField(default=1)

    # Skip an entry if live price has moved more than this % past the signal entry.
    max_slippage_pct = models.FloatField(default=0.5)
    # Don't act on a signal older than this many seconds.
    max_signal_age_sec = models.PositiveIntegerField(default=600)

    # Only auto-trade signals at/above this confidence (in addition to the global
    # SIGNAL_MIN_CONFIDENCE the feed already enforces). 0 = use the global floor.
    min_confidence = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} auto-trade ({'on' if self.enabled else 'off'})"


class TradeExecution(models.Model):
    """One attempt to act on a signal for a user. Unique on (user, signal) so a
    re-run / duplicate task never double-places the same trade."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        REJECTED = "rejected", "Rejected by broker"
        SKIPPED = "skipped", "Skipped"
        ERROR = "error", "Error"

    class CloseReason(models.TextChoices):
        TP = "tp", "Take-profit"
        SL = "sl", "Stop-loss"
        INVALIDATED = "invalidated", "Signal invalidated"
        MANUAL = "manual", "Manual / kill switch"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trade_executions"
    )
    signal = models.ForeignKey(
        "signals.Signal", on_delete=models.CASCADE, related_name="executions"
    )
    broker_connection = models.ForeignKey(
        BrokerConnection, on_delete=models.SET_NULL, null=True, related_name="executions"
    )

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    # Populated when status is SKIPPED/REJECTED/ERROR (e.g. "slippage", "unmapped symbol").
    detail = models.CharField(max_length=300, blank=True, default="")

    side = models.CharField(max_length=4, blank=True, default="")  # Buy / Sell
    bybit_symbol = models.CharField(max_length=32, blank=True, default="")
    qty = models.FloatField(null=True, blank=True)
    leverage = models.PositiveSmallIntegerField(null=True, blank=True)
    intended_entry = models.FloatField(null=True, blank=True)
    fill_price = models.FloatField(null=True, blank=True)

    # Exchange order ids for the entry + protective orders, for reconciliation.
    order_ids = models.JSONField(default=dict)  # {"entry": "...", "sl": "...", "tp": [...]}

    realized_pnl = models.FloatField(null=True, blank=True)
    close_reason = models.CharField(
        max_length=12, choices=CloseReason.choices, blank=True, default=""
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    # Reconciler bookkeeping. sl_moved_to_be makes the break-even SL move
    # idempotent (only amend the stop once); last_reconciled_at is for
    # observability / stuck-position alerting.
    sl_moved_to_be = models.BooleanField(default=False)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "signal"], name="uniq_user_signal_execution")
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} · signal {self.signal_id} ({self.status})"
