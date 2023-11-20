"""URLs.

Remember that they are all relative to /plugins/wjs_stats/.
"""

from django.urls import path

from .plugin_settings import MANAGER_URL
from .views import MuninProxy, RecipientsCount, StatsView, manager

urlpatterns = [
    path("manager/", manager, name=MANAGER_URL),
    path("stats/", StatsView.as_view(), name="wjs_stats"),
    path("recipients-count/", RecipientsCount.as_view(), name="wjs_stats_recipients_count"),
    path("munin-proxy/<str:server>/<str:image>/", MuninProxy.as_view(), name="wjs_stats_munin_proxy"),
]
