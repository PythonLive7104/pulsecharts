from django.urls import path

from .views import CandlesView, SymbolListView

urlpatterns = [
    path("symbols/", SymbolListView.as_view(), name="symbol-list"),
    path("symbols/<str:ticker>/candles/", CandlesView.as_view(), name="symbol-candles"),
]
