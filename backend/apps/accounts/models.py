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

    # Email verification. New signups start unverified and cannot obtain an auth
    # token until they click the emailed link (see CustomTokenObtainPairSerializer).
    # Existing users at rollout are backfilled to True (migration 0012) — they were
    # already using the product, so re-verifying them would lock them out. Staff-
    # created / superuser accounts are also treated as verified.
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)

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

    # Last time the recurring "upgrade to keep getting signals" Telegram nudge went
    # out. Distinct from plan_expiry_notified_for, which fires ONCE per lapse: this
    # one repeats on an interval for as long as the user has no signal access, so it
    # has to be a plain timestamp rather than keyed to an expiry value. Also covers
    # never-paid users, who have no expiry to key on at all.
    upgrade_nudge_sent_at = models.DateTimeField(null=True, blank=True)

    # Referral code used at signup (attribution); the grant itself is applied to
    # plan_tier/plan_expiry at registration time.
    # Opted out of MARKETING email (campaigns). Never gates transactional mail —
    # verification, password resets and payment receipts are sent because the user
    # asked for them, and suppressing those would break the product.
    marketing_opt_out = models.BooleanField(default=False)

    referred_by_code = models.CharField(max_length=40, blank=True, default="")
    # Earnings (whole USD) from people who signed up with this user's own code;
    # redeemable toward a plan once it reaches the plan's price.
    referral_credits = models.PositiveIntegerField(default=0)
    # The admin Pro-promo code this user has already redeemed (settings.ADMIN_PRO_CODE
    # at redeem time). Guards one grant per code value — if the admin rotates the
    # code, the stored value differs and the user can redeem the new one.
    pro_promo_code_used = models.CharField(max_length=64, blank=True, default="")
    # Same guard for the admin Starter-promo code (settings.ADMIN_STARTER_CODE).
    # Tracked separately from the Pro code so a user can redeem each once.
    starter_promo_code_used = models.CharField(max_length=64, blank=True, default="")

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
        # The money came back out of our account. Both revoke the grant this row
        # paid for (apps.billing.views), and the row is kept — never deleted — so
        # there's evidence to contest the chargeback with.
        DISPUTED = "disputed", "Disputed (chargeback)"
        REFUNDED = "refunded", "Refunded"

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


class WithdrawalRequest(models.Model):
    """A referrer asking to be paid out their commission balance in USDT (TRC20).

    Accounting rule that makes this safe: creating a request ATTACHES the pending
    commissions it covers (ReferralCommission.withdrawal). Those rows are then spoken
    for, so a second request can't claim the same money and commissions earned after
    the request roll into the next one. Rejecting a request detaches them again.
    """

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        PAID = "PAID", "Paid"
        REJECTED = "REJECTED", "Rejected"

    NETWORK = "USDT-TRC20"

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="withdrawals"
    )
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2)
    wallet_address = models.CharField(max_length=64)
    network = models.CharField(max_length=32, default=NETWORK)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.REQUESTED, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    tx_hash = models.CharField(max_length=128, blank=True, default="")
    admin_note = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user.email} · ${self.amount_usd} · {self.status}"

    def mark_paid(self, tx_hash: str = "") -> int:
        """Settle the request AND the commissions behind it, in one step.

        Marking only the request paid would leave its commissions PENDING, so the
        same money would be offered for withdrawal again next time.
        """
        from django.utils import timezone as _tz

        if tx_hash:
            self.tx_hash = tx_hash
        self.status = self.Status.PAID
        self.paid_at = _tz.now()
        self.save(update_fields=["status", "paid_at", "tx_hash"])
        return self.commissions.update(
            status=ReferralCommission.Status.PAID, paid_at=self.paid_at
        )

    def reject(self, note: str = "") -> int:
        """Release the commissions so the balance is withdrawable again."""
        self.status = self.Status.REJECTED
        if note:
            self.admin_note = note
        self.save(update_fields=["status", "admin_note"])
        return self.commissions.update(withdrawal=None)


class ReferralCommission(models.Model):
    """A cash commission owed to a referrer because someone they referred PAID.

    Distinct from `User.referral_credits`, and deliberately so:

      * credits are earned at SIGNUP ($1), live on the user row, and are spent
        in-app by redeeming them for a plan — they never leave the system as money;
      * a commission is earned when the referred user actually PAYS, is a share of
        real revenue, and is settled OUT of band (bank transfer, crypto, whatever)
        and then marked paid here.

    Mixing the two would let someone redeem a plan with money you still owe them in
    cash, so they are separate ledgers on purpose.

    One row per payment: `payment_ref` is unique, so a replayed Paystack webhook
    can never pay a referrer twice for the same charge.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending payout"
        PAID = "PAID", "Paid"
        VOID = "VOID", "Void (refunded/disputed)"

    referrer = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="referral_commissions"
    )
    # The payer. SET_NULL so deleting a user never erases what you owe someone else;
    # the row keeps `referred_email` for the record.
    referred_user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="commissions_generated",
    )
    referred_email = models.EmailField(blank=True, default="")
    code = models.CharField(max_length=40, blank=True, default="")

    payment_ref = models.CharField(max_length=128, unique=True)
    plan = models.CharField(max_length=16)              # starter | pro | lifetime
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2)   # what they paid
    rate_pct = models.DecimalField(max_digits=5, decimal_places=2)      # e.g. 20.00
    commission_usd = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # The payout request this commission was rolled into, if any. Set when a
    # withdrawal is requested so the same earnings can't be claimed twice; cleared
    # again if that request is rejected.
    withdrawal = models.ForeignKey(
        "accounts.WithdrawalRequest", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="commissions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Free-text payout reference (bank transfer id, tx hash, "paid by hand 5 Aug").
    payout_note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["referrer", "status"])]

    def __str__(self):
        return f"{self.referrer.email} · ${self.commission_usd} · {self.status}"

    def mark_paid(self, note: str = "") -> None:
        from django.utils import timezone as _tz

        self.status = self.Status.PAID
        self.paid_at = _tz.now()
        if note:
            self.payout_note = note
        self.save(update_fields=["status", "paid_at", "payout_note"])


class ReferralCode(models.Model):
    """A code that grants a temporary plan when used at signup.

    Default: 30 days of Starter. After expiry the plan logic (apps.accounts.plans)
    automatically treats the user as Free again — no downgrade job needed. Manage
    codes in the Django admin.
    """

    code = models.CharField(max_length=40, unique=True)
    is_active = models.BooleanField(default=True)
    # When True, a new user signing up with this code gets `grant_tier` for
    # `grant_days`. Default flipped to True on 2026-08-05 (migration 0015, which
    # also backfilled every existing code): EVERY referral link now hands the new
    # user 30 days of Starter, not just the admin code.
    #
    # It stays a per-code flag rather than a global setting so a single code can be
    # switched off — e.g. one being abused for repeat free trials — without turning
    # the offer off for everyone.
    grants_signup_plan = models.BooleanField(default=True)
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

        from django.conf import settings

        user.referred_by_code = self.code
        fields = ["referred_by_code"]
        # Attribution (and the owner's $1) always applies; only the PLAN GRANT is
        # switchable. REFERRAL_SIGNUP_GRANT_ENABLED ends the promo globally without
        # rewriting any code's flag, so it can be turned back on unchanged.
        grant_on = getattr(settings, "REFERRAL_SIGNUP_GRANT_ENABLED", True)
        if self.grants_signup_plan and grant_on:
            # Never shorten access the user somehow already has: a null expiry on a
            # paid tier means "never expires", and a longer expiry outranks this
            # grant. Normally a no-op at signup — this only guards the case where
            # redeem() is applied to an account that already has a plan.
            granted_until = timezone.now() + timedelta(days=self.grant_days)
            paid_forever = user.plan_expiry is None and user.plan_tier != PlanTier.FREE
            if not paid_forever and (
                user.plan_expiry is None or user.plan_expiry < granted_until
            ):
                user.plan_tier = self.grant_tier
                user.plan_expiry = granted_until
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
