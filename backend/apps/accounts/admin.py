from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import ReferralCode, Subscription, User
from .tasks import trim_to_plan_limits


def _trim_summary(result) -> str:
    return (
        f"watchlist -{result['watchlist']}, strategies -{result['strategies']}, "
        f"layouts -{result['layouts']}"
    )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "plan_tier", "plan_expiry", "is_staff")
    list_filter = ("plan_tier", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "dodo_customer_id", "referred_by_code")
    actions = ["enforce_plan_limits_action"]

    def save_model(self, request, obj, form, change):
        """Persist the user, then bring saved data back within the (possibly newly
        lowered) plan's limits — the same trim a real billing downgrade runs — so
        changing plan_tier here doesn't leave a stale over-limit watchlist / strategy
        follows. No-op on an upgrade or when nothing changed."""
        super().save_model(request, obj, form, change)
        result = trim_to_plan_limits(obj)
        if result["watchlist"] or result["layouts"] or result["strategies"]:
            self.message_user(
                request, f"Enforced plan limits for {obj.email}: {_trim_summary(result)}."
            )

    @admin.action(description="Enforce plan limits (trim watchlist / strategies / layouts)")
    def enforce_plan_limits_action(self, request, queryset):
        users = wl = strat = lay = 0
        for user in queryset:
            r = trim_to_plan_limits(user)
            if r["watchlist"] or r["layouts"] or r["strategies"]:
                users += 1
                wl += r["watchlist"]
                strat += r["strategies"]
                lay += r["layouts"]
        self.message_user(
            request,
            f"Enforced limits on {users} user(s): "
            f"watchlist -{wl}, strategies -{strat}, layouts -{lay}.",
        )
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        ("Plan", {"fields": ("plan_tier", "plan_expiry", "dodo_customer_id",
                              "referred_by_code", "referral_credits")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "renewal_date", "payment_ref")
    list_filter = ("tier", "status")
    search_fields = ("user__email", "payment_ref")


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = (
        "code", "owner", "is_active", "grants_signup_plan",
        "grant_tier", "grant_days", "used_count", "max_uses", "note",
    )
    list_filter = ("is_active", "grants_signup_plan", "grant_tier")
    search_fields = ("code", "note", "owner__email")
    raw_id_fields = ("owner",)
    readonly_fields = ("used_count", "created_at")
