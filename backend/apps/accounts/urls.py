from django.urls import path

from .views import (
    ChangePasswordView,
    EntitlementsView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PlansView,
    RedeemPromoCodeView,
    ReferralRedeemView,
    ReferralSetCodeView,
    ReferralView,
    RegisterView,
    TelegramDisconnectView,
    TelegramReconnectView,
    TelegramStatusView,
    TelegramWebhookView,
)
from .verification import ResendVerificationView, VerifyEmailView

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/verify-email/resend/", ResendVerificationView.as_view(), name="verify-email-resend"),
    path("auth/password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("me/", MeView.as_view(), name="me"),
    path("me/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("me/entitlements/", EntitlementsView.as_view(), name="entitlements"),
    path("me/referral/", ReferralView.as_view(), name="referral"),
    path("me/referral/code/", ReferralSetCodeView.as_view(), name="referral-set-code"),
    path("me/referral/redeem/", ReferralRedeemView.as_view(), name="referral-redeem"),
    path("me/referral/redeem-code/", RedeemPromoCodeView.as_view(), name="referral-redeem-code"),
    path("me/telegram/", TelegramStatusView.as_view(), name="telegram-status"),
    path("me/telegram/disconnect/", TelegramDisconnectView.as_view(), name="telegram-disconnect"),
    path("me/telegram/reconnect/", TelegramReconnectView.as_view(), name="telegram-reconnect"),
    path("telegram/webhook/<str:secret>/", TelegramWebhookView.as_view(), name="telegram-webhook"),
    path("plans/", PlansView.as_view(), name="plans"),
]
