from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import ReferralCode, Subscription, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "plan_tier", "plan_expiry", "is_staff")
    list_filter = ("plan_tier", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "dodo_customer_id", "referred_by_code")
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
