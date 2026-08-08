"""Marketing email campaigns (bulk, opt-out-able) — distinct from transactional mail.

Transactional email (verification, password reset, payment receipts) is sent because
the user did something and needs the result. This is ADVERTISING, which brings rules
transactional mail doesn't have:

  * it must be throttled (a new domain that suddenly sends hundreds of marketing
    emails gets its reputation shredded and lands in spam for everyone);
  * it must honour an unsubscribe, permanently and without a login;
  * it must not be sent to someone it's irrelevant to — advertising the lifetime plan
    to a user who already bought it is the fastest way to look careless.

All three are enforced in tasks.run_campaign_sends, not left to whoever hits Send.
"""

from __future__ import annotations

from django.db import models


class EmailCampaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft (not sending)"
        SENDING = "SENDING", "Sending"
        PAUSED = "PAUSED", "Paused"
        DONE = "DONE", "Finished"

    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=200)
    # Raw HTML, pasted or uploaded in the admin. Supports the placeholders listed in
    # PLACEHOLDERS below; anything else is passed through untouched.
    html_body = models.TextField(
        help_text="Full HTML. Placeholders: {{email}} {{name}} {{lifetime_url}} "
                  "{{billing_url}} {{unsubscribe_url}} {{price}}"
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    # Per-campaign override of settings.CAMPAIGN_DAILY_CAP. The GLOBAL cap still
    # applies on top — this can only make a campaign slower, never the sender louder.
    daily_cap = models.PositiveIntegerField(default=40)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="campaigns_created",
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.name} ({self.status})"

    @property
    def counts(self) -> dict:
        qs = self.recipients.values("status").annotate(n=models.Count("id"))
        out = {row["status"]: row["n"] for row in qs}
        out["total"] = sum(out.values())
        return out


class CampaignRecipient(models.Model):
    """One planned send. Unique per (campaign, user) so re-adding a selection in the
    admin can never queue somebody twice."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"

    campaign = models.ForeignKey(
        EmailCampaign, on_delete=models.CASCADE, related_name="recipients"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="campaign_emails"
    )
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "user"], name="uniq_campaign_recipient"
            )
        ]
        indexes = [models.Index(fields=["campaign", "status"])]

    def __str__(self):
        return f"{self.campaign_id} -> {self.user_id} ({self.status})"
