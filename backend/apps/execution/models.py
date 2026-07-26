"""Auto-trade / broker execution models (v2, gated OFF by default).

Turns a delivered Signal into a real bracket order on the user's own Bybit account.
Two models:

- ``BrokerCredential`` — a user's encrypted Bybit API key/secret plus their personal
  risk envelope (fixed margin per trade, max concurrent positions, daily cap). One
  row per user. Secrets are stored via ``apps.execution.crypto`` (Fernet), never in
  plaintext, and never returned by the API.
- ``TradeExecution`` — one row per (user, signal) we acted on: the placed order, the
  sizing that produced it, and its lifecycle (open → closed/liquidated), or the
  reason it was skipped/rejected. Backs dedup (unique per user+signal), the
  max-open-positions gate, and the daily-trade counter.

This whole subsystem is inert unless ``settings.AUTO_TRADE_ENABLED`` is true AND a
user has an enabled credential — see ``apps.execution.executor``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from . import crypto


class BrokerCredential(models.Model):
    """A user's broker connection + per-user risk envelope. One per user."""

    class Broker(models.TextChoices):
        BYBIT = "bybit", "Bybit"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broker_credential"
    )
    broker = models.CharField(max_length=16, choices=Broker.choices, default=Broker.BYBIT)

    # Fernet-encrypted at rest (apps.execution.crypto). Set/read via the properties
    # below; the raw columns are never exposed by the serializer.
    api_key_enc = models.TextField(blank=True, default="")
    api_secret_enc = models.TextField(blank=True, default="")

    # Bybit testnet vs mainnet. Testnet keys hit api-testnet.bybit.com and move no
    # real money — the safe default for a user trying the feature out.
    testnet = models.BooleanField(default=True)

    # The user's master on/off switch. Even with AUTO_TRADE_ENABLED globally on, a
    # user only gets orders placed while this is True and keys are set.
    enabled = models.BooleanField(default=False)

    # --- per-user risk envelope (override the AUTO_TRADE_* settings defaults) ---
    # Fixed margin committed per trade, in USDT. Small by design so a tiny account
    # can hold several positions (see apps.execution.sizing).
    margin_per_trade_usd = models.FloatField(default=1.0)
    # Nullable overrides — null means "use the settings default". Kept nullable (not
    # just defaulted) so a global tightening of the defaults reaches users who never
    # customised their envelope.
    max_open_positions = models.PositiveSmallIntegerField(null=True, blank=True)
    max_daily_trades = models.PositiveSmallIntegerField(null=True, blank=True)
    # Hard ceiling on leverage the sizer may pick, regardless of what the symbol
    # allows. Caps liquidation risk even if a symbol permits 100x.
    max_leverage = models.PositiveSmallIntegerField(default=10)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        state = "on" if self.enabled else "off"
        return f"{self.user_id} · {self.broker} ({state})"

    # --- secret handling -------------------------------------------------------
    def set_api_key(self, value: str) -> None:
        self.api_key_enc = crypto.encrypt(value or "")

    def set_api_secret(self, value: str) -> None:
        self.api_secret_enc = crypto.encrypt(value or "")

    @property
    def api_key(self) -> str:
        return crypto.decrypt(self.api_key_enc)

    @property
    def api_secret(self) -> str:
        return crypto.decrypt(self.api_secret_enc)

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key_enc and self.api_secret_enc)

    # --- resolved risk envelope (override → settings default) ------------------
    @property
    def effective_max_open_positions(self) -> int:
        if self.max_open_positions is not None:
            return self.max_open_positions
        return settings.AUTO_TRADE_MAX_OPEN_POSITIONS

    @property
    def effective_max_daily_trades(self) -> int:
        if self.max_daily_trades is not None:
            return self.max_daily_trades
        return settings.AUTO_TRADE_MAX_DAILY_TRADES

    @property
    def is_active(self) -> bool:
        """Ready to place: switched on and holding both keys. (Premium + global
        AUTO_TRADE_ENABLED are checked separately in the executor.)"""
        return self.enabled and self.has_keys


class TradeExecution(models.Model):
    """One record per (user, signal) the executor acted on — placed, skipped, or
    errored — plus the resulting position's lifecycle."""

    class Status(models.TextChoices):
        # Order accepted by Bybit; position is (or was) live.
        OPEN = "open", "Open"
        # Position no longer on the exchange (TP/SL hit or manually closed).
        CLOSED = "closed", "Closed"
        # Position was liquidated — margin lost before the stop was reached.
        LIQUIDATED = "liquidated", "Liquidated"
        # A risk gate or sizing check blocked the trade before any order was sent.
        SKIPPED = "skipped", "Skipped"
        # An order WAS attempted but Bybit rejected it (or the request errored).
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trade_executions"
    )
    # SET_NULL (not CASCADE): resolved signals are purged after the retention window
    # (apps.signals.tasks.run_purge), but the execution record — a real order that
    # moved a user's money — must outlive it. Denormalised fields below keep the row
    # readable after the signal is gone.
    signal = models.ForeignKey(
        "signals.Signal", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="executions",
    )

    # Denormalised trade identity (survives signal purge; also the dedup key).
    bybit_symbol = models.CharField(max_length=32)
    direction = models.CharField(max_length=8)  # "BUY" | "SELL"
    timeframe = models.CharField(max_length=8, blank=True, default="")

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SKIPPED, db_index=True
    )

    # --- sizing snapshot (from apps.execution.sizing.plan_order) ---
    leverage = models.PositiveSmallIntegerField(default=0)
    qty = models.FloatField(default=0.0)
    notional = models.FloatField(default=0.0)   # qty × entry, USDT
    margin = models.FloatField(default=0.0)      # what the trade tied up
    entry_price = models.FloatField(default=0.0)
    stop_loss = models.FloatField(default=0.0)
    take_profit = models.FloatField(null=True, blank=True)  # first TP (ladder) or the single bracket TP
    liq_price = models.FloatField(default=0.0)

    # True when this trade was placed as a 50/25/25 reduce-only take-profit ladder
    # (§19.2) rather than a single-TP bracket. False when the position was too small
    # to split into placeable tranches and fell back to one full-position TP.
    scaleout = models.BooleanField(default=False)
    # Set once the stop has been trailed to breakeven — i.e. a TP tranche filled and
    # the reconciler moved the stop to entry, so the runner can't turn into a loss.
    breakeven_moved = models.BooleanField(default=False)

    # --- broker linkage ---
    # Client-generated idempotency key sent to Bybit (orderLinkId). Deterministic per
    # (user, signal) so a retried place never double-fills. Unique to enforce that.
    order_link_id = models.CharField(max_length=64, blank=True, default="")
    bybit_order_id = models.CharField(max_length=64, blank=True, default="")

    # Populated on SKIPPED/REJECTED (Telegram-friendly), else blank.
    reason = models.CharField(max_length=200, blank=True, default="")
    # Realized P&L in USDT once the position closes (from reconcile), if Bybit reports it.
    realized_pnl = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One action per user per signal — the executor's dedup. Only meaningful
            # while the signal exists; a purged signal (signal_id NULL) is exempt so
            # historical rows don't collide.
            models.UniqueConstraint(
                fields=["user", "signal"],
                condition=models.Q(signal__isnull=False),
                name="uniq_user_signal_execution",
            ),
            models.UniqueConstraint(
                fields=["order_link_id"],
                condition=~models.Q(order_link_id=""),
                name="uniq_order_link_id",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} · {self.direction} {self.bybit_symbol} [{self.status}]"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN
