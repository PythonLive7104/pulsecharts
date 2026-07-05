"""Accounts: custom User + Subscription (Section 8).

User is extended with plan/billing fields; entitlements (Section 11) are derived
from plan_tier + plan_expiry. Email is the login identifier.
"""

from datetime import timedelta

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class PlanTier(models.TextChoices):
    FREE = "free", "Free"
    STARTER = "starter", "Starter"
    PRO = "pro", "Pro"
    PREMIUM = "premium", "Premium (legacy)"  # kept for back-compat; maps to Pro


class UserManager(BaseUserManager):
    """Email-as-username manager."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    # Drop username; email is the identifier.
    username = None
    email = models.EmailField("email address", unique=True)

    plan_tier = models.CharField(
        max_length=16, choices=PlanTier.choices, default=PlanTier.FREE
    )
    plan_expiry = models.DateTimeField(null=True, blank=True)
    dodo_customer_id = models.CharField(max_length=128, blank=True, default="")

    # Telegram signal delivery: chat_id is set once the user links via the bot's
    # /start deep link; link_token is the one-time payload in that deep link, and
    # link_token_at is when it was issued (the token expires after a short TTL so
    # a forwarded link can't be redeemed by a stranger later).
    #
    # telegram_active is the on/off switch for delivery. Disconnecting flips this
    # to False but KEEPS the chat_id, because Telegram won't re-send /start for a
    # chat that already started the bot (no START button appears the second time),
    # which made deep-link reconnects silently fail. Remembering the chat lets the
    # dashboard reconnect in one click with no Telegram round-trip.
    #
    # Defaults to False: the flag is only meaningful once a chat is linked (linking
    # sets it True). Never trust it alone as "Telegram connected" — use the
    # telegram_connected property, which also checks chat_id.
    telegram_chat_id = models.CharField(max_length=32, blank=True, default="", db_index=True)
    telegram_active = models.BooleanField(default=False)
    telegram_link_token = models.CharField(max_length=64, blank=True, default="")
    telegram_link_token_at = models.DateTimeField(null=True, blank=True)

    # The plan_expiry value we've already sent a "your plan expired" Telegram
    # notice for. Keyed on the datetime (not a bare bool) so it self-re-arms: when
    # a user resubscribes, plan_expiry moves to a new future value that differs
    # from this, so the next lapse notifies again — no reset needed at grant time.
    plan_expiry_notified_for = models.DateTimeField(null=True, blank=True)

    # Referral code used at signup (attribution); the grant itself is applied to
    # plan_tier/plan_expiry at registration time.
    referred_by_code = models.CharField(max_length=40, blank=True, default="")
    # Earnings (whole USD) from people who signed up with this user's own code;
    # redeemable toward a plan once it reaches the plan's price.
    referral_credits = models.PositiveIntegerField(default=0)
    # The admin Pro-promo code this user has already redeemed (settings.ADMIN_PRO_CODE
    # at redeem time). Guards one grant per code value — if the admin rotates the
    # code, the stored value differs and the user can redeem the new one.
    pro_promo_code_used = models.CharField(max_length=64, blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def is_premium(self) -> bool:
        """True on any active paid tier (Starter or Pro), expiry-aware.

        Kept under the original name for back-compat — lots of callers treat it as
        "is this a paying user". Fine-grained gating reads the plan matrix instead
        (apps.accounts.plans).
        """
        from .plans import is_paid

        return is_paid(self)

    @property
    def plan_key(self) -> str:
        """Effective plan key honoring expiry: 'free' | 'starter' | 'pro'."""
        from .plans import plan_key

        return plan_key(self)

    @property
    def telegram_connected(self) -> bool:
        return bool(self.telegram_chat_id) and self.telegram_active

    @property
    def telegram_can_reconnect(self) -> bool:
        """A chat we still remember but that delivery is currently switched off
        for — reconnectable in one click without going back through Telegram."""
        return bool(self.telegram_chat_id) and not self.telegram_active

    # A connect link is good for this long after it's issued. Long enough that a
    # user who taps "Connect", switches to the Telegram app, and presses Start a
    # bit later (or disconnects and reconnects) still lands on a valid link — the
    # 30s status poll re-mints the token at this boundary, so too short a TTL
    # rotated the token out from under an in-flight link and broke reconnects.
    # Forwarded-link abuse is still bounded by one-time use + the
    # already-linked-to-another-chat guard in the webhook.
    TELEGRAM_LINK_TOKEN_TTL = timedelta(hours=1)

    def _telegram_token_fresh(self) -> bool:
        return bool(
            self.telegram_link_token
            and self.telegram_link_token_at
            and timezone.now() - self.telegram_link_token_at <= self.TELEGRAM_LINK_TOKEN_TTL
        )

    def ensure_telegram_link_token(self) -> str:
        """Return a currently-valid one-time deep-link token, minting a fresh one
        if there isn't one or it has expired."""
        if not self._telegram_token_fresh():
            import secrets

            self.telegram_link_token = secrets.token_urlsafe(24)
            self.telegram_link_token_at = timezone.now()
            self.save(update_fields=["telegram_link_token", "telegram_link_token_at"])
        return self.telegram_link_token

    def telegram_token_valid(self, token: str) -> bool:
        """True if `token` matches and is within the TTL window."""
        return bool(token) and token == self.telegram_link_token and self._telegram_token_fresh()

    def ensure_referral_code(self):
        """Return this user's personal ReferralCode, creating one if needed.

        New signups that use it grant the new user Starter (30 days) AND credit
        this user $1. The code is auto-generated from the email but can be
        customized (admin or the set-code endpoint).
        """
        import re
        import secrets

        rc = self.referral_codes.first()
        if rc:
            return rc
        base = re.sub(r"[^A-Z0-9]", "", self.email.split("@")[0].upper())[:16] or "USER"
        for _ in range(10):
            code = f"{base}_{secrets.randbelow(9000) + 1000}"
            if not ReferralCode.objects.filter(code=code).exists():
                break
        else:
            code = f"REF_{secrets.token_hex(4).upper()}"
        return ReferralCode.objects.create(code=code, owner=self)


class Subscription(models.Model):
    """Billing record kept in sync by the Dodo webhook (Section 8)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="subscriptions"
    )
    tier = models.CharField(
        max_length=16, choices=PlanTier.choices, default=PlanTier.PREMIUM
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    renewal_date = models.DateTimeField(null=True, blank=True)
    payment_ref = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} · {self.tier} · {self.status}"


class ReferralCode(models.Model):
    """A code that grants a temporary plan when used at signup.

    Default: 30 days of Starter. After expiry the plan logic (apps.accounts.plans)
    automatically treats the user as Free again — no downgrade job needed. Manage
    codes in the Django admin.
    """

    code = models.CharField(max_length=40, unique=True)
    is_active = models.BooleanField(default=True)
    # When True, a new user signing up with this code gets the grant plan for
    # grant_days (e.g. MAILIONDEV_7788 → 30-day Starter). When False (the default
    # for ordinary personal codes) the code still credits the owner $1, but the
    # new user stays on Free.
    grants_signup_plan = models.BooleanField(default=False)
    grant_tier = models.CharField(
        max_length=16, choices=PlanTier.choices, default=PlanTier.STARTER
    )
    grant_days = models.PositiveIntegerField(default=30)
    max_uses = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    used_count = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True, default="")
    # Set for personal (user) codes; null for admin promo codes. Personal codes
    # credit the owner $1 each time a new user signs up with them.
    owner = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.CASCADE,
        related_name="referral_codes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Dollars the owner earns each time their code is used at signup.
    REWARD_USD = 1

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()  # normalize — codes are case-insensitive
        super().save(*args, **kwargs)

    @property
    def is_usable(self) -> bool:
        return self.is_active and (self.max_uses == 0 or self.used_count < self.max_uses)

    def redeem(self, user) -> None:
        """Apply a code at signup. Caller should check `is_usable` first.

        - Always records attribution + bumps used_count.
        - Always credits the owner $1 (personal codes; never self-refer).
        - ONLY grants the new user a temporary plan when grants_signup_plan is set
          (e.g. MAILIONDEV_7788). Ordinary personal codes leave the new user Free.
        """
        from datetime import timedelta

        from django.db.models import F
        from django.utils import timezone

        user.referred_by_code = self.code
        fields = ["referred_by_code"]
        if self.grants_signup_plan:
            user.plan_tier = self.grant_tier
            user.plan_expiry = timezone.now() + timedelta(days=self.grant_days)
            fields += ["plan_tier", "plan_expiry"]
        user.save(update_fields=fields)

        ReferralCode.objects.filter(pk=self.pk).update(used_count=F("used_count") + 1)

        # Credit the referrer (personal codes only; never self-refer).
        if self.owner_id and self.owner_id != user.id:
            User.objects.filter(pk=self.owner_id).update(
                referral_credits=F("referral_credits") + self.REWARD_USD
            )

    def __str__(self):
        return self.code
