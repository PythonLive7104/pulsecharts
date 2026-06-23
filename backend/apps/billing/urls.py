from django.urls import path

from .views import CheckoutView, SubscriptionHistoryView, WebhookView

urlpatterns = [
    path("checkout/", CheckoutView.as_view(), name="billing-checkout"),
    path("webhook/", WebhookView.as_view(), name="billing-webhook"),
    path("history/", SubscriptionHistoryView.as_view(), name="billing-history"),
]
