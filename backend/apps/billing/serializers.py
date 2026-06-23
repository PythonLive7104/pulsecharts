"""Serializers for the billing API."""

from rest_framework import serializers

from apps.accounts.models import Subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    """A user's subscription record, as written by the Dodo webhook. Read-only —
    billing state is only ever mutated server-side from verified webhook events."""

    tier_label = serializers.CharField(source="get_tier_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id", "tier", "tier_label", "status", "status_label",
            "renewal_date", "payment_ref", "created_at", "updated_at",
        ]
        read_only_fields = fields
