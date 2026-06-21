from django.urls import path

from .views import ChartLayoutDetailView, ChartLayoutListView, WorkspaceView

urlpatterns = [
    path("chart-layouts/", ChartLayoutListView.as_view(), name="chart-layouts"),
    path(
        "chart-layouts/<int:pk>/",
        ChartLayoutDetailView.as_view(),
        name="chart-layout-detail",
    ),
    path("me/workspace/", WorkspaceView.as_view(), name="workspace"),
]
