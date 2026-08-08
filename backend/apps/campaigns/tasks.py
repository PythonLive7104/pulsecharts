"""Daily drain of queued campaign emails.

Everything that protects the sending domain and the recipient lives here rather than
in the admin, so nobody can bypass it by clicking Send twice.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("campaigns")


def _skip_reason(user) -> str | None:
    """Why this user must not receive marketing mail right now, or None."""
    from apps.accounts.plans import is_lifetime_purchaser

    if getattr(user, "marketing_opt_out", False):
        return "unsubscribed"
    if not user.is_active:
        return "inactive account"
    # Unverified addresses are the ones most likely to bounce, and bounces are what
    # wreck a sending domain's reputation.
    if not user.email_verified:
        return "email not verified"
    # Never advertise the lifetime plan to someone who already owns it.
    if is_lifetime_purchaser(user):
        return "already owns lifetime"
    return None


def run_campaign_sends() -> dict:
    """Send up to the daily cap, newest campaigns last. Returns a summary."""
    from apps.common.email import send_email

    from .models import CampaignRecipient, EmailCampaign
    from .render import render_html

    global_cap = int(getattr(settings, "CAMPAIGN_DAILY_CAP", 40) or 0)
    if global_cap <= 0:
        return {"sent": 0, "skipped": "campaign sending disabled (cap 0)"}

    cooldown_days = int(getattr(settings, "CAMPAIGN_MIN_DAYS_BETWEEN", 7) or 0)
    now = timezone.now()
    # Global cap is per CALENDAR DAY of actual sends, counted from the ledger rather
    # than a counter — so a restart mid-run can't reset the budget and double it.
    sent_today = CampaignRecipient.objects.filter(
        status=CampaignRecipient.Status.SENT, sent_at__date=now.date()
    ).count()
    budget = global_cap - sent_today
    if budget <= 0:
        return {"sent": 0, "note": f"daily cap {global_cap} already reached"}

    sent = failed = skipped = 0
    for campaign in EmailCampaign.objects.filter(status=EmailCampaign.Status.SENDING):
        if budget <= 0:
            break
        per_campaign = min(budget, campaign.daily_cap)
        queue = (
            CampaignRecipient.objects
            .filter(campaign=campaign, status=CampaignRecipient.Status.PENDING)
            .select_related("user")[: per_campaign * 3]  # headroom for skips
        )
        for rec in queue:
            if budget <= 0:
                break
            user = rec.user

            reason = _skip_reason(user)
            if reason:
                rec.status = CampaignRecipient.Status.SKIPPED
                rec.note = reason
                rec.save(update_fields=["status", "note"])
                skipped += 1
                continue

            # One marketing email per user per cooldown window, across ALL campaigns —
            # otherwise two active campaigns would each send them one.
            if cooldown_days:
                last = (
                    CampaignRecipient.objects
                    .filter(user=user, status=CampaignRecipient.Status.SENT)
                    .order_by("-sent_at").values_list("sent_at", flat=True).first()
                )
                if last and now - last < timedelta(days=cooldown_days):
                    continue  # left PENDING: it becomes eligible again later

            ok = send_email(
                to=user.email,
                subject=campaign.subject,
                html=render_html(campaign.html_body, user),
            )
            if ok:
                rec.status = CampaignRecipient.Status.SENT
                rec.sent_at = timezone.now()
                rec.save(update_fields=["status", "sent_at"])
                sent += 1
                budget -= 1
            else:
                rec.status = CampaignRecipient.Status.FAILED
                rec.note = "send failed"
                rec.save(update_fields=["status", "note"])
                failed += 1

        # Nothing left that could ever send -> the campaign is finished.
        if not CampaignRecipient.objects.filter(
            campaign=campaign, status=CampaignRecipient.Status.PENDING
        ).exists():
            campaign.status = EmailCampaign.Status.DONE
            campaign.save(update_fields=["status"])

    if sent or failed:
        logger.info("campaign sends: sent=%d failed=%d skipped=%d", sent, failed, skipped)
    return {"sent": sent, "failed": failed, "skipped": skipped,
            "cap": global_cap, "already_sent_today": sent_today}


@shared_task(name="apps.campaigns.tasks.send_campaign_emails")
def send_campaign_emails() -> dict:
    return run_campaign_sends()
