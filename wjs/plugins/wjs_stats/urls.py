"""URLs.

Remember that they are all relative to /plugins/wjs_stats/.
"""

from django.urls import path

from .plugin_settings import MANAGER_URL
from .views import DOIsCount, Manager, MuninProxy, RecipientsCount, StatsView

urlpatterns = [
    path("manager/", Manager.as_view(), name=MANAGER_URL),
    path("stats/", StatsView.as_view(), name="wjs_stats"),
    path("recipients-count/", RecipientsCount.as_view(), name="wjs_stats_recipients_count"),
    path("dois-count/", DOIsCount.as_view(), name="wjs_stats_dois_count"),
    path("munin-proxy/<str:server>/<str:image>/", MuninProxy.as_view(), name="wjs_stats_munin_proxy"),
]
