"""Trading-signals data model (Section 13.4, 19).

v2 feature. Signals are generated server-side on a schedule (Celery, Section 20),
stored, and served to users who follow the strategy that produced them — capped
by their plan's weekly quota (enforced server-side, Section 13.3).
"""

from django.conf import settings
from django.db import models

from apps.market_data.models import Symbol


class SignalService(models.Model):
    """An algorithmic strategy ("signal service") users can follow (13.2).

    Built-in strategies have ``owner=None`` and are dispatched by ``slug`` to the
    hardcoded rules in ``pregate``. User-created (Pro) strategies have an ``owner``
    and a declarative ``rule_config`` evaluated generically by ``strategy_builder``
    — see ``pregate.candidate_direction_for_service``.
    """

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True, default="")
    # Plain-English strategy focus, injected into the Claude prompt (Section 20).
    strategy_focus = models.TextField(blank=True, default="")
    strategy_type = models.CharField(max_length=40, blank=True, default="")
    is_active = models.BooleanField(default=True)

    # Custom (user-created) strategies. owner=None => built-in system strategy.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="custom_strategies",
    )
    # Declarative rule for custom strategies (strategy_builder schema); None for
    # built-in strategies (which run hardcoded Python keyed by slug).
    rule_config = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_custom(self) -> bool:
        return self.owner_id is not None


class StrategyCreationLog(models.Model):
    """Append-only record of each custom-strategy creation, used to enforce the
    rolling-30-day creation cap. Deliberately NOT deleted when the strategy is
    deleted — the cap is on *creating* strategies, not on how many are active, so
    deleting one never refunds a slot."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="strategy_creations"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user_id} created a strategy @ {self.created_at:%Y-%m-%d}"


class Signal(models.Model):
    """A generated trading signal (full card spec, Section 19.1)."""

    class Direction(models.TextChoices):
        BUY = "BUY", "Buy"
        SELL = "SELL", "Sell"
        NEUTRAL = "NEUTRAL", "Neutral"

    symbol = models.ForeignKey(Symbol, on_delete=models.CASCADE, related_name="signals")
    service = models.ForeignKey(SignalService, on_delete=models.CASCADE, related_name="signals")
    direction = models.CharField(max_length=8, choices=Direction.choices)
    confidence_pct = models.PositiveSmallIntegerField()
    timeframe = models.CharField(max_length=8)
    generated_at = models.DateTimeField(db_index=True)

    entry_price = models.FloatField()
    stop_loss = models.FloatField()
    tp1 = models.FloatField()
    tp2 = models.FloatField()
    tp3 = models.FloatField()
    tp4 = models.FloatField(null=True, blank=True)  # removed from the ladder (1R/2R/3R)

    risk_pct = models.FloatField()
    reward_tp1_pct = models.FloatField()
    reward_tp2_pct = models.FloatField()
    reward_tp3_pct = models.FloatField()
    reward_tp4_pct = models.FloatField(null=True, blank=True)

    risk_reward_tp1 = models.FloatField()
    risk_reward_tp2 = models.FloatField()
    risk_reward_tp3 = models.FloatField()
    risk_reward_tp4 = models.FloatField(null=True, blank=True)

    dollar_risk = models.FloatField()
    dollar_tp1 = models.FloatField()
    dollar_tp2 = models.FloatField()
    dollar_tp3 = models.FloatField()
    dollar_tp4 = models.FloatField(null=True, blank=True)

    reasoning = models.TextField(blank=True, default="")
    invalidation = models.TextField(blank=True, default="")

    # Does the DAILY 200 EMA support this signal's direction? True when price is on
    # the trend-supporting side of the daily 200 EMA at generation time (above it for
    # a BUY, below it for a SELL). A higher-timeframe confirmation shown on the card.
    # Nullable: unknown for legacy rows, and for symbols without enough daily history
    # to compute a 200 EMA (new listings) or when the daily fetch failed.
    daily_ema200_aligned = models.BooleanField(null=True, blank=True)

    # --- outcome tracking (Section 13.7, 18, 20.5) ---
    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Pending"
        TP1 = "TP1", "Hit TP1"
        TP2 = "TP2", "Hit TP2"
        TP3 = "TP3", "Hit TP3"
        TP4 = "TP4", "Hit TP4"
        SL = "SL", "Stopped out"
        EXPIRED = "EXPIRED", "Expired"
        # Trend flipped before TP or SL was hit: the call is closed flat at 0 P/L
        # — NOT a loss. Only an actual stop-loss hit (SL) counts as a stopped-out /
        # losing trade.
        INVALIDATED = "INVALID", "Invalidated — trend flipped"

    outcome = models.CharField(
        max_length=8, choices=Outcome.choices, default=Outcome.PENDING, db_index=True
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    mfe_pct = models.FloatField(null=True, blank=True)  # max favorable excursion %
    mae_pct = models.FloatField(null=True, blank=True)  # max adverse excursion %
    # Highest take-profit reached SO FAR (0 = none yet). Tracked on every evaluator
    # pass, including while the trade is still PENDING: under "let winners run"
    # (§19.2) a call that banks TP1/TP2 stays open until it reaches TP3 or comes back
    # to the breakeven stop, so `outcome` alone can't tell a user their partial is
    # due. This is what drives the "TP1 tagged — take your partial" push and the live
    # progress on the card.
    best_tp = models.PositiveSmallIntegerField(default=0)
    # When best_tp last increased — i.e. when the newest target was tagged. Lets the
    # dashboard timestamp a "TP2 tagged" event and interleave it with closures in one
    # chronological Trade-updates list, the same way Telegram sends them.
    best_tp_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]
        indexes = [models.Index(fields=["service", "-generated_at"])]

    def __str__(self):
        return f"{self.direction} {self.symbol.ticker} {self.timeframe} ({self.confidence_pct}%)"


class UserSignalSubscription(models.Model):
    """Which strategies a user follows (13.4)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signal_subscriptions"
    )
    service = models.ForeignKey(SignalService, on_delete=models.CASCADE, related_name="subscribers")
    subscribed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "service"], name="uniq_user_service")
        ]

    def __str__(self):
        return f"{self.user_id} -> {self.service.slug}"


class SignalDelivery(models.Model):
    """Records that a signal was shown to a user — backs weekly-quota enforcement
    and prevents re-showing the same signal twice (13.3, 13.4)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signal_deliveries"
    )
    signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="deliveries")
    delivered_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "signal"], name="uniq_user_signal_delivery")
        ]
        indexes = [models.Index(fields=["user", "delivered_at"])]

    def __str__(self):
        return f"{self.user_id} <- signal {self.signal_id}"


class TelegramDelivery(models.Model):
    """Records that a signal was pushed to a user's Telegram. Separate from
    SignalDelivery (in-app feed) so the two channels don't interfere, and so the
    same signal is never sent to Telegram twice."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_deliveries"
    )
    signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="telegram_deliveries")
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Set once we've pushed the "trade update" (TP/SL/invalidated) for this signal,
    # so the closure notice is sent at most once.
    closure_notified = models.BooleanField(default=False)
    # Highest TP we've already pushed a "target tagged" notice for. Progress pushes
    # fire while the trade is still open (Signal.best_tp > tp_notified), so the user
    # hears "take your partial" when it's actionable rather than only at closure.
    tp_notified = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "signal"], name="uniq_user_signal_telegram")
        ]
        indexes = [models.Index(fields=["user", "sent_at"])]

    def __str__(self):
        return f"{self.user_id} <-tg- signal {self.signal_id}"
