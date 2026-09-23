"""Admin review for manual crypto payments.

The approve action is the ONLY place a crypto payment turns into access, and it
routes through billing.grant.apply_paid_grant — the same function the Paystack
webhook uses — so a crypto payment produces an identical Subscription row, referral
commission, provisioning top-up and confirmation email.
"""

from django.contrib import admin, messages
from django.utils import timezone

from .grant import apply_paid_grant
from .models import CryptoPayment


@admin.register(CryptoPayment)
class CryptoPaymentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "plan", "asset", "amount_usd", "status", "reviewed_by")
    list_filter = ("status", "asset", "plan")
    search_fields = ("user__email", "tx_hash", "payment_ref")
    # Everything the payment WAS is immutable after submission: editing the plan or
    # amount after the fact would let a review grant something the user never paid
    # for, and the tx hash is the receipt.
    readonly_fields = (
        "user", "plan", "asset", "address", "amount_usd", "tx_hash", "note",
        "created_at", "reviewed_at", "reviewed_by", "payment_ref",
    )
    fields = readonly_fields + ("status", "review_note")
    actions = ("approve_payments", "reject_payments")

    @admin.action(description="Approve selected payments — grants the plan")
    def approve_payments(self, request, queryset):
        granted = skipped = failed = 0
        # Only PENDING rows: re-approving an APPROVED row would be a no-op thanks to
        # the payment_ref idempotency, but silently "succeeding" on an already-granted
        # payment hides double-clicks rather than reporting them.
        for row in queryset.select_related("user"):
            if row.status == CryptoPayment.Status.APPROVED:
                skipped += 1
                continue
            # Reference is derived, not user-supplied, and is unique per claim — it is
            # what makes apply_paid_grant idempotent and what ties the Subscription
            # row back to this review.
            reference = row.payment_ref or f"crypto-{row.asset.lower()}-{row.pk}"
            try:
                apply_paid_grant(
                    row.user, row.plan, reference, int(round(float(row.amount_usd) * 100))
                )
            except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
                failed += 1
                self.message_user(
                    request, f"{row.user.email}: grant failed — {exc}", messages.ERROR
                )
                continue
            row.status = CryptoPayment.Status.APPROVED
            row.reviewed_at = timezone.now()
            row.reviewed_by = request.user
            row.payment_ref = reference
            row.save(update_fields=["status", "reviewed_at", "reviewed_by", "payment_ref"])
            granted += 1

        if granted:
            self.message_user(request, f"Granted {granted} payment(s).", messages.SUCCESS)
        if skipped:
            self.message_user(
                request, f"Skipped {skipped} already-approved payment(s).", messages.WARNING
            )
        if failed:
            self.message_user(request, f"{failed} failed — see errors above.", messages.ERROR)

    @admin.action(description="Reject selected payments")
    def reject_payments(self, request, queryset):
        # Never un-grant here: revoking access is a separate, deliberate action
        # (billing revoke / set_plan), and a mis-click on a bulk reject must not strip
        # a plan someone actually paid for.
        n = queryset.exclude(status=CryptoPayment.Status.APPROVED).update(
            status=CryptoPayment.Status.REJECTED,
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )
        self.message_user(request, f"Rejected {n} payment(s).", messages.SUCCESS)
        if queryset.filter(status=CryptoPayment.Status.APPROVED).exists():
            self.message_user(
                request,
                "Already-approved payments were left alone — revoke access separately.",
                messages.WARNING,
            )
