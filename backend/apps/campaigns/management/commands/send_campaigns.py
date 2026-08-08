"""Run the campaign sender now, without waiting for the scheduled task.

Useful for the first send (so you can watch it work rather than wonder), and as the
manual lever if Celery beat isn't running. Obeys every limit the scheduled run does —
the daily cap, the per-user cooldown, and the skip rules — so triggering it by hand
can't send more than the automated path would.
"""

from django.core.management.base import BaseCommand

from apps.campaigns.models import CampaignRecipient, EmailCampaign
from apps.campaigns.tasks import run_campaign_sends


class Command(BaseCommand):
    help = "Send queued campaign emails now (respects the daily cap and cooldown)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would be sent without sending anything.")

    def handle(self, *args, **opts):
        w = self.stdout.write

        campaigns = EmailCampaign.objects.all()
        if not campaigns:
            w(self.style.WARNING("No campaigns exist."))
            return

        w(self.style.MIGRATE_HEADING("Campaigns"))
        for c in campaigns:
            counts = c.counts
            flag = "" if c.status == EmailCampaign.Status.SENDING else \
                "   <- NOT sending; use the 'Start sending' action"
            w(f"  {c.name!r}  status={c.status}  auto_enroll={c.auto_enroll}  "
              f"pending={counts.get('PENDING', 0)} sent={counts.get('SENT', 0)}{flag}")

        sending = campaigns.filter(status=EmailCampaign.Status.SENDING)
        if not sending:
            w(self.style.ERROR(
                "\nNothing is in SENDING status, so nothing will go out. Tick the "
                "campaign in the admin and run the 'Start sending' action."))
            return

        if opts["dry_run"]:
            n = CampaignRecipient.objects.filter(
                campaign__in=sending, status=CampaignRecipient.Status.PENDING
            ).count()
            w(f"\nDry run: {n} pending recipient(s) across {sending.count()} campaign(s).")
            return

        w("")
        w(str(run_campaign_sends()))
