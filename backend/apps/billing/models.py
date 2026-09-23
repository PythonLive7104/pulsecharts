"""Manual crypto payments.

Paystack declined this account — trading sites fall outside their acceptable-use
policy — so subscriptions are paid by direct transfer to a wallet and approved by
staff in the admin.

The deliberate design choice here is that NOTHING is automatic. There is no chain
watcher and no auto-confirm: a user submits a claim, a human checks the transaction
on-chain, and only then does the plan get granted. That is slower than a payment
processor and it is the right trade for a low volume of high-value payments, because
the failure mode of an automated crypto confirmation (granting access off a
mistyped, duplicated or spoofed tx hash) is worse than the failure mode of a delay.

Approval reuses the SAME grant path as Paystack (billing.grant.apply_paid_grant),
so a crypto payment and a card payment produce an identical Subscription row,
referral commission, provisioning top-up and confirmation email. Duplicating that
logic would guarantee the two drift.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class CryptoPayment(models.Model):
    """A user's claim that they have paid, plus its review state."""

    class Asset(models.TextChoices):
        USDT = "USDT", "USDT"
        BTC = "BTC", "BTC"
        LTC = "LTC", "LTC"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        APPROVED = "APPROVED", "Approved — plan granted"
        REJECTED = "REJECTED", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="crypto_payments"
    )
    # The purchase option bought ("starter" / "pro" / "lifetime") — validated against
    # PURCHASE_OPTIONS on submit, and re-read at approval so an edited row cannot
    # grant something that was never offered.
    plan = models.CharField(max_length=20)
    asset = models.CharField(max_length=8, choices=Asset.choices)
    # The address shown to the user at the time. Stored rather than looked up later:
    # rotating a wallet must not make old claims unverifiable.
    address = models.CharField(max_length=120)
    # USD price of the plan when the claim was made, so a later price change does not
    # retroactively make a paid claim look short.
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2)
    tx_hash = models.CharField(
        max_length=200,
        help_text="Transaction hash / ID the user says they paid with.",
    )
    note = models.TextField(blank=True, default="")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="crypto_payments_reviewed",
    )
    review_note = models.TextField(blank=True, default="")
    # Set once approved, matching Subscription.payment_ref. Approval is idempotent on
    # it, so double-clicking the admin action cannot grant twice.
    payment_ref = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]
        constraints = [
            # One claim per transaction: the same hash must not be submitted twice,
            # by the same user or a different one.
            models.UniqueConstraint(fields=["asset", "tx_hash"], name="uniq_asset_txhash"),
        ]

    def __str__(self):
        return f"{self.user_id} · {self.plan} · {self.asset} · {self.status}"
