"""Landing-page support chat (Section 1 — visitor help).

Two public endpoints, both unauthenticated (the chat lives on the public
landing page):
  POST /api/support/chat/     — answer a question from the curated knowledge base.
  POST /api/support/contact/  — email a message to the support inbox.

The chat is NOT an LLM — answers come from apps/support/knowledge.py. Anything it
can't match steers the visitor to the contact form.
"""

import logging

from django.conf import settings
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.common.email import send_contact_message

from .knowledge import SUGGESTED_QUESTIONS, answer_question

logger = logging.getLogger("support")

_MAX_MESSAGE = 2000


class ChatView(APIView):
    """POST /api/support/chat/ — {message} -> {reply, matched, suggestions}."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [AnonRateThrottle]

    def get(self, request):
        # Lets the widget fetch starter chips on open.
        return Response({"suggestions": SUGGESTED_QUESTIONS})

    def post(self, request):
        message = str(request.data.get("message", ""))[:_MAX_MESSAGE]
        reply, matched, suggestions = answer_question(message)
        return Response(
            {"reply": reply, "matched": matched, "suggestions": suggestions}
        )


class ContactView(APIView):
    """POST /api/support/contact/ — {email, message} -> emails the support inbox."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        message = str(request.data.get("message", "")).strip()[:_MAX_MESSAGE]

        try:
            validate_email(email)
        except ValidationError:
            return Response(
                {"detail": "Please enter a valid email address."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not message:
            return Response(
                {"detail": "Please enter a message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inbox = settings.CONTACT_US_EMAIL
        if not inbox:
            # Misconfiguration — don't tell the visitor it silently dropped.
            logger.error("Contact form submitted but CONTACT_US_EMAIL is unset.")
            return Response(
                {"detail": "Contact is temporarily unavailable. Please try later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        sent = send_contact_message(to=inbox, from_email=email, message=message)
        if not sent:
            return Response(
                {"detail": "We couldn't send your message right now. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"detail": "Thanks! We'll get back to you by email soon."})
