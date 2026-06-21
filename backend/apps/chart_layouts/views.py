from rest_framework import generics
from rest_framework.exceptions import ValidationError

from .models import ChartLayout, Workspace, layout_limit_for
from .serializers import ChartLayoutSerializer, WorkspaceSerializer


class ChartLayoutListView(generics.ListCreateAPIView):
    """GET/POST /api/chart-layouts/ (Section 9)."""

    serializer_class = ChartLayoutSerializer

    def get_queryset(self):
        return ChartLayout.objects.filter(user=self.request.user).select_related(
            "symbol"
        )

    def perform_create(self, serializer):
        user = self.request.user
        limit = layout_limit_for(user)
        if ChartLayout.objects.filter(user=user).count() >= limit:
            raise ValidationError(
                f"Saved-layout limit reached ({limit}). Upgrade to save more."
            )
        serializer.save(user=user)


class ChartLayoutDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/chart-layouts/{id}/."""

    serializer_class = ChartLayoutSerializer

    def get_queryset(self):
        return ChartLayout.objects.filter(user=self.request.user)


class WorkspaceView(generics.RetrieveUpdateAPIView):
    """GET/PUT /api/me/workspace/ — the user's cross-device workspace.

    Auto-created on first access; last write wins.
    """

    serializer_class = WorkspaceSerializer

    def get_object(self):
        obj, _ = Workspace.objects.get_or_create(user=self.request.user)
        return obj
