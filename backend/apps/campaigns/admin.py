"""Campaign admin: write it, choose who gets it, preview it, queue it.

Sending is never immediate. "Start sending" only flips the campaign to SENDING; the
daily task drains it at the capped rate. That's deliberate — a Send button that
actually sends is one misclick away from mailing every user at once.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import escape, format_html

from apps.accounts.models import User

from .models import CampaignRecipient, EmailCampaign
from .render import PLACEHOLDERS, preview_html


class RecipientInline(admin.TabularInline):
    model = CampaignRecipient
    extra = 0
    fields = ("user", "status", "sent_at", "note")
    readonly_fields = ("user", "sent_at", "note")
    # A campaign can hold thousands of rows; the inline is for spot-checking, not
    # browsing. Use the CampaignRecipient changelist for the full list.
    max_num = 0
    can_delete = True
    show_change_link = False


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "subject", "daily_cap", "progress", "preview_link", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "subject")
    readonly_fields = ("created_at", "created_by", "progress", "placeholder_help",
                       "recipients_help")
    inlines = [RecipientInline]
    actions = ["add_recipients", "start_sending", "pause_sending"]
    fieldsets = (
        (None, {"fields": ("name", "subject", "status", "daily_cap")}),
        ("Content", {"fields": ("placeholder_help", "html_body")}),
        ("Recipients", {"fields": ("recipients_help", "progress")}),
        ("Meta", {"fields": ("created_by", "created_at")}),
    )

    @admin.display(description="Available placeholders")
    def placeholder_help(self, obj):
        return format_html(
            "<code>{}</code><br><small>Substituted per recipient when the email is "
            "sent. Include <code>{{{{unsubscribe_url}}}}</code> — bulk mail without "
            "a working unsubscribe is what gets a sending domain blocked.</small>",
            "  ".join(PLACEHOLDERS),
        )

    @admin.display(description="How to add recipients")
    def recipients_help(self, obj):
        if not obj.pk:
            return format_html(
                "<b>Save this campaign first</b>, then add recipients one of two ways:"
                "<ul style='margin:6px 0 0 16px'>"
                "<li>Campaigns → Email campaigns → tick this campaign → action "
                "<b>“Add recipients…”</b> (whole audiences, e.g. everyone on Free)</li>"
                "<li>Accounts → Users → tick specific users → action "
                "<b>“Add selected users to an email campaign”</b></li></ul>"
            )
        url = reverse("admin:campaigns_emailcampaign_changelist")
        return format_html(
            "Use the <b>“Add recipients…”</b> action on the "
            "<a href='{}'>campaign list</a> to add a whole audience, or pick "
            "individuals in Accounts → Users → <b>“Add selected users to an email "
            "campaign”</b>.", url,
        )

    @admin.display(description="Progress")
    def progress(self, obj):
        if not obj.pk:
            return "—"
        c = obj.counts
        return (f"{c.get('SENT', 0)} sent · {c.get('PENDING', 0)} pending · "
                f"{c.get('SKIPPED', 0)} skipped · {c.get('FAILED', 0)} failed "
                f"({c.get('total', 0)} total)")

    @admin.display(description="Preview")
    def preview_link(self, obj):
        if not obj.pk:
            return "—"
        url = reverse("admin:campaigns_emailcampaign_preview", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">Open preview ↗</a>', url)

    def get_urls(self):
        return [
            path(
                "<int:pk>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="campaigns_emailcampaign_preview",
            ),
        ] + super().get_urls()

    def preview_view(self, request, pk):
        """Render the campaign exactly as a recipient would receive it.

        Rendered with sample values rather than a real user so opening a preview can
        never leak one customer's details to whoever is looking at the admin.
        """
        campaign = EmailCampaign.objects.filter(pk=pk).first()
        if campaign is None:
            return HttpResponse("Campaign not found", status=404)
        return HttpResponse(preview_html(campaign.html_body))

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Add recipients… (choose an audience)")
    def add_recipients(self, request, queryset):
        """Bulk-add an audience. The per-user action on the Users list stays for
        hand-picking; this is for "everyone on Free" without paging a changelist."""
        from apps.accounts.plans import plan_key

        if queryset.count() != 1:
            self.message_user(
                request, "Pick exactly one campaign to add recipients to.",
                level=messages.ERROR,
            )
            return
        campaign = queryset.first()

        # Only users who could actually receive it — the send task would skip the
        # rest anyway, and queueing them just fills the list with SKIPPED rows.
        from .tasks import _skip_reason

        eligible = [u for u in User.objects.all() if _skip_reason(u) is None]
        buckets = {
            "all": eligible,
            "free": [u for u in eligible if plan_key(u) == "free"],
            "starter": [u for u in eligible if plan_key(u) == "starter"],
            "pro": [u for u in eligible if plan_key(u) == "pro"],
        }

        chosen = request.POST.get("audience")
        if chosen in buckets:
            users = buckets[chosen]
            rows = [CampaignRecipient(campaign=campaign, user=u) for u in users]
            created = CampaignRecipient.objects.bulk_create(rows, ignore_conflicts=True)
            self.message_user(
                request,
                f"Added {len(users)} user(s) to “{campaign.name}” ({len(created)} new). "
                "Nothing sends until the campaign status is Sending.",
            )
            return redirect(request.get_full_path())

        labels = {
            "all": "Everyone eligible",
            "free": "Free users only",
            "starter": "Starter users only",
            "pro": "Pro users only (excludes lifetime owners)",
        }
        opts = "".join(
            f"<label style='display:block;margin:8px 0;'>"
            f"<input type='radio' name='audience' value='{k}'{' checked' if k == 'all' else ''}> "
            f"{labels[k]} — <b>{len(v)}</b> user(s)</label>"
            for k, v in buckets.items()
        )
        selected = f"<input type='hidden' name='_selected_action' value='{campaign.pk}'>"
        return HttpResponse(f"""
<!doctype html><meta charset="utf-8">
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;">
  <h2>Add recipients to “{escape(campaign.name)}”</h2>
  <p style="color:#555">Already unsubscribed, unverified or lifetime-owning users are
     excluded — the sender would skip them anyway.</p>
  <form method="post">
    <input type="hidden" name="csrfmiddlewaretoken" value="{request.COOKIES.get('csrftoken', '')}">
    <input type="hidden" name="action" value="add_recipients">
    {selected}
    {opts}
    <p style="margin-top:18px;">
      <button type="submit" style="padding:9px 18px;font-weight:600;">Add to campaign</button>
      <a href="{escape(request.get_full_path())}" style="margin-left:12px;">Cancel</a>
    </p>
  </form>
</body>""")

    @admin.action(description="Start sending (queued, capped per day)")
    def start_sending(self, request, queryset):
        blocked = [c.name for c in queryset if "{{unsubscribe_url}}" not in c.html_body]
        if blocked:
            self.message_user(
                request,
                "Not started — no {{unsubscribe_url}} in: " + ", ".join(blocked)
                + ". Bulk mail must carry a working unsubscribe link.",
                level=messages.ERROR,
            )
            return
        n = queryset.exclude(status=EmailCampaign.Status.DONE).update(
            status=EmailCampaign.Status.SENDING
        )
        self.message_user(
            request,
            f"{n} campaign(s) queued. Sending runs daily within the cap — nothing "
            "goes out this instant.",
        )

    @admin.action(description="Pause sending")
    def pause_sending(self, request, queryset):
        n = queryset.filter(status=EmailCampaign.Status.SENDING).update(
            status=EmailCampaign.Status.PAUSED
        )
        self.message_user(request, f"Paused {n} campaign(s).")


@admin.register(CampaignRecipient)
class CampaignRecipientAdmin(admin.ModelAdmin):
    list_display = ("campaign", "user", "status", "sent_at", "note")
    list_filter = ("status", "campaign")
    search_fields = ("user__email", "campaign__name")
    readonly_fields = ("campaign", "user", "sent_at")
    actions = ["requeue"]

    @admin.action(description="Re-queue (send again on the next run)")
    def requeue(self, request, queryset):
        n = queryset.update(status=CampaignRecipient.Status.PENDING, sent_at=None, note="")
        self.message_user(request, f"Re-queued {n} recipient(s).")


# --- adding users to a campaign, from the User changelist -------------------

@admin.action(description="Add selected users to an email campaign")
def add_to_campaign(modeladmin, request, queryset):
    """Two-step: pick users on the User list, then choose the campaign.

    Rendered inline rather than via a template file so the whole feature stays in
    this app — there is no templates/ directory in this project to hang it off.
    """
    campaigns = EmailCampaign.objects.exclude(status=EmailCampaign.Status.DONE)
    if not campaigns:
        modeladmin.message_user(
            request, "Create a campaign first (Campaigns → Email campaigns → Add).",
            level=messages.ERROR,
        )
        return

    if request.POST.get("campaign"):
        campaign = campaigns.filter(pk=request.POST["campaign"]).first()
        if campaign is None:
            modeladmin.message_user(request, "Campaign not found.", level=messages.ERROR)
            return
        rows = [CampaignRecipient(campaign=campaign, user=u) for u in queryset]
        # ignore_conflicts: the unique constraint makes re-adding the same selection a
        # no-op instead of an error, so an admin can safely widen a selection later.
        created = CampaignRecipient.objects.bulk_create(rows, ignore_conflicts=True)
        modeladmin.message_user(
            request,
            f"Added {len(queryset)} user(s) to “{campaign.name}” "
            f"({len(created)} new). Nothing sends until the campaign is set to Sending.",
        )
        return redirect(request.get_full_path())

    options = "".join(
        f'<option value="{c.pk}">{escape(c.name)} — {escape(c.status)}</option>'
        for c in campaigns
    )
    selected = "".join(
        f'<input type="hidden" name="_selected_action" value="{u.pk}">' for u in queryset
    )
    return HttpResponse(f"""
<!doctype html><meta charset="utf-8">
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;">
  <h2>Add {len(queryset)} user(s) to a campaign</h2>
  <p style="color:#555">They'll be queued as PENDING. Nothing is sent until the
     campaign's status is <b>Sending</b>, and then only within the daily cap.</p>
  <form method="post">
    <input type="hidden" name="csrfmiddlewaretoken" value="{request.COOKIES.get('csrftoken', '')}">
    <input type="hidden" name="action" value="add_to_campaign">
    {selected}
    <p><select name="campaign" style="padding:8px;min-width:320px;">{options}</select></p>
    <p>
      <button type="submit" style="padding:9px 18px;font-weight:600;">Add to campaign</button>
      <a href="{escape(request.get_full_path())}" style="margin-left:12px;">Cancel</a>
    </p>
  </form>
</body>""")


# Attach the action to the existing User admin without subclassing it.
admin.site._registry[User].actions = list(
    getattr(admin.site._registry[User], "actions", []) or []
) + [add_to_campaign]
