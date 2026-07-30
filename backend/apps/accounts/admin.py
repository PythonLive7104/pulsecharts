from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q
from django.utils import timezone

from .models import ReferralCode, Subscription, User
from .plans import PAID_TIER_VALUES, PAID_TIERS, PLANS, plan_key
from .tasks import trim_to_plan_limits


def _trim_summary(result) -> str:
    return (
        f"watchlist -{result['watchlist']}, strategies -{result['strategies']}, "
        f"layouts -{result['layouts']}"
    )


class AccessFilter(admin.SimpleListFilter):
    """Filter by EFFECTIVE access rather than the stored tier.

    `plan_tier` keeps whatever was last granted — a lapsed plan is resolved to Free at
    read time (plans.plan_key), never rewritten — so filtering on the raw column
    counts expired users as paying ones.
    """

    title = "access (effective)"
    parameter_name = "access"

    def lookups(self, request, model_admin):
        return [
            ("paid", "Paying now (not expired)"),
            ("expired", "Lapsed (paid tier, expiry passed)"),
            ("free", "Free"),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        paid = queryset.filter(plan_tier__in=PAID_TIER_VALUES)
        if self.value() == "paid":
            # A null expiry on a paid tier means "never expires" (lifetime/staff grant).
            return paid.filter(Q(plan_expiry__isnull=True) | Q(plan_expiry__gt=now))
        if self.value() == "expired":
            return paid.filter(plan_expiry__lte=now)
        if self.value() == "free":
            return queryset.exclude(plan_tier__in=PAID_TIER_VALUES)
        return queryset


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    # `plan_tier` is what was last GRANTED; `effective_plan` is what the user
    # actually gets today. They differ for every lapsed plan — see effective_plan().
    list_display = ("email", "effective_plan", "plan_tier", "plan_expiry", "is_staff")
    list_filter = (AccessFilter, "plan_tier", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "dodo_customer_id", "referred_by_code")
    actions = ["enforce_plan_limits_action"]

    @admin.display(description="Effective plan", ordering="plan_tier")
    def effective_plan(self, obj):
        """What the app actually grants this user right now.

        Expiry is enforced at READ time (plans.plan_key) rather than by rewriting
        plan_tier, so a lapsed Starter row still reads "Starter" in the raw column
        while the user is being served Free everywhere. This column shows the truth
        (and keeps the stored tier visible as history of what they last paid for).
        """
        key = plan_key(obj)
        label = PLANS[key]["label"]
        if obj.plan_tier in PAID_TIER_VALUES and key not in PAID_TIERS:
            # .get(): the legacy "premium" value has no PLANS entry of its own.
            stored = PLANS.get(obj.plan_tier, {}).get("label") or obj.plan_tier.title()
            return f"{label} (lapsed {stored})"
        return label

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
