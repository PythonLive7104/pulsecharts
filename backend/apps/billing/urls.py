from django.urls import path

from .views import (
    CheckoutView,
    CryptoPaymentView,
    CryptoWalletsView,
    SubscriptionHistoryView,
    WebhookView,
)

urlpatterns = [
    path("checkout/", CheckoutView.as_view(), name="billing-checkout"),
    path("webhook/", WebhookView.as_view(), name="billing-webhook"),
    path("history/", SubscriptionHistoryView.as_view(), name="billing-history"),
    # Manual crypto payments (Paystack declined this account — trading sites are
    # outside their acceptable-use policy). Submitting a claim grants nothing; staff
    # approve in the admin after checking the chain.
    path("crypto/wallets/", CryptoWalletsView.as_view(), name="billing-crypto-wallets"),
    path("crypto/claim/", CryptoPaymentView.as_view(), name="billing-crypto-claim"),
]
