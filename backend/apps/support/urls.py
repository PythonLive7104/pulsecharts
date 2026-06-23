from django.urls import path

from .views import ChatView, ContactView

urlpatterns = [
    path("support/chat/", ChatView.as_view(), name="support-chat"),
    path("support/contact/", ContactView.as_view(), name="support-contact"),
]
