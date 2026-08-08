from django.urls import path

from .views import UnsubscribeView

urlpatterns = [
    path("campaigns/unsubscribe/<str:token>/", UnsubscribeView.as_view(), name="campaign-unsubscribe"),
]
