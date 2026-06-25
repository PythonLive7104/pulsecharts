"""Trading-signals data model (Section 13.4, 19).

v2 feature. Signals are generated server-side on a schedule (Celery, Section 20),
stored, and served to users who follow the strategy that produced them — capped
by their plan's daily quota (enforced server-side, Section 13.3).
"""

from django.conf import settings
from django.db import models

from apps.market_data.models import Symbol


class SignalService(models.Model):
    """An algorithmic strategy ("signal service") users can follow (13.2)."""

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True, default="")
    # Plain-English strategy focus, injected into the Claude prompt (Section 20).
    strategy_focus = models.TextField(blank=True, default="")
    strategy_type = models.CharField(max_length=40, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


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
    tp4 = models.FloatField()

    risk_pct = models.FloatField()
    reward_tp1_pct = models.FloatField()
    reward_tp2_pct = models.FloatField()
    reward_tp3_pct = models.FloatField()
    reward_tp4_pct = models.FloatField()

    risk_reward_tp1 = models.FloatField()
    risk_reward_tp2 = models.FloatField()
    risk_reward_tp3 = models.FloatField()
    risk_reward_tp4 = models.FloatField()

    dollar_risk = models.FloatField()
    dollar_tp1 = models.FloatField()
    dollar_tp2 = models.FloatField()
    dollar_tp3 = models.FloatField()
    dollar_tp4 = models.FloatField()

    reasoning = models.TextField(blank=True, default="")
    invalidation = models.TextField(blank=True, default="")

    # --- outcome tracking (Section 13.7, 18, 20.5) ---
    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Pending"
        TP1 = "TP1", "Hit TP1"
        TP2 = "TP2", "Hit TP2"
        TP3 = "TP3", "Hit TP3"
        TP4 = "TP4", "Hit TP4"
        SL = "SL", "Stopped out"
        EXPIRED = "EXPIRED", "Expired"
        # Trend flipped before TP or SL was hit: the call is closed flat. Treated
        # as a breakeven close (0% P/L) — NOT a loss. Only an actual stop-loss hit
        # (SL) counts as a stopped-out / losing trade.
        INVALIDATED = "INVALID", "Closed — breakeven (trend flipped)"

    outcome = models.CharField(
        max_length=8, choices=Outcome.choices, default=Outcome.PENDING, db_index=True
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    mfe_pct = models.FloatField(null=True, blank=True)  # max favorable excursion %
    mae_pct = models.FloatField(null=True, blank=True)  # max adverse excursion %

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
    """Records that a signal was shown to a user — backs daily-quota enforcement
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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "signal"], name="uniq_user_signal_telegram")
        ]
        indexes = [models.Index(fields=["user", "sent_at"])]

    def __str__(self):
        return f"{self.user_id} <-tg- signal {self.signal_id}"
