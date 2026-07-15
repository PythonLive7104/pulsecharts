"""Root URL configuration.

API surface mirrors Section 9 of CLAUDE.md. The WS endpoint (/ws/market/) is
wired in config/routing.py, not here.
"""

from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.verification import VerifiedTokenObtainPairView

api_patterns = [
    # auth (JWT for the React SPA). Login refuses unverified accounts — see
    # apps/accounts/serializers.VerifiedTokenObtainPairSerializer.
    path("auth/token/", VerifiedTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # domain apps
    path("", include("apps.accounts.urls")),
    path("", include("apps.market_data.urls")),
    path("", include("apps.watchlists.urls")),
    path("", include("apps.chart_layouts.urls")),
    path("", include("apps.signals.urls")),
    path("", include("apps.alerts.urls")),
    path("", include("apps.support.urls")),
    path("billing/", include("apps.billing.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
]
