"""Price alerts API (Section 12).

GET    /api/me/alerts/            list the user's alerts (active + triggered)
POST   /api/me/alerts/            create an alert
DELETE /api/me/alerts/{id}/       delete an alert
POST   /api/me/alerts/seen/       mark triggered alerts as seen (clears the badge)
GET    /api/me/alerts/unseen/     count of unseen triggered alerts (for the badge)
"""

from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PriceAlert
from .serializers import PriceAlertSerializer


class AlertListCreateView(generics.ListCreateAPIView):
    serializer_class = PriceAlertSerializer

    def get_queryset(self):
        return PriceAlert.objects.filter(user=self.request.user).select_related("symbol")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class AlertDeleteView(generics.DestroyAPIView):
    def get_queryset(self):
        return PriceAlert.objects.filter(user=self.request.user)


class AlertSeenView(APIView):
    def post(self, request):
        updated = PriceAlert.objects.filter(
            user=request.user, triggered_at__isnull=False, seen=False
        ).update(seen=True)
        return Response({"marked_seen": updated})


class AlertUnseenCountView(APIView):
    def get(self, request):
        n = PriceAlert.objects.filter(
            user=request.user, triggered_at__isnull=False, seen=False
        ).count()
        return Response({"unseen": n})
