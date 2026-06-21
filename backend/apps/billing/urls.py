from django.urls import path

from .views import CheckoutView, WebhookView

urlpatterns = [
    path("checkout/", CheckoutView.as_view(), name="billing-checkout"),
    path("webhook/", WebhookView.as_view(), name="billing-webhook"),
]
