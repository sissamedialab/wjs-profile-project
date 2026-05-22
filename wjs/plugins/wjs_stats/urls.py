"""
URLs.

Remember that they are all relative to /plugins/wjs_stats/.
"""

from django.urls import path

from . import views
from .plugin_settings import MANAGER_URL

urlpatterns = [
    path("manager/", views.Manager.as_view(), name=MANAGER_URL),
    path("stats/", views.StatsView.as_view(), name="wjs_stats"),
    path("recipients-count/", views.RecipientsCount.as_view(), name="wjs_stats_recipients_count"),
    path("dois-count/", views.DOIsCount.as_view(), name="wjs_stats_dois_count"),
    path("munin-proxy/<str:server>/<str:image>/", views.MuninProxy.as_view(), name="wjs_stats_munin_proxy"),
    path("articles/", views.ArticlesStatsView.as_view(), name="wjs_stats_articles"),
    path("typesetters/", views.TypesettersStatsView.as_view(), name="wjs_stats_typesetters"),
    path(
        "double-accounts/",
        views.DoubleAccountsView.as_view(),
        name="wjs_stats_double_accounts",
    ),
    path("orcids/", views.OrcidsStatsView.as_view(), name="wjs_stats_orcids"),
    path(
        "submissions-and-publications.tsv",
        views.SubmittedPublishedPerMonthTSV.as_view(),
        name="wjs_stats_submissions_and_publications_tsv",
    ),
    path(
        "submissions-and-publications/",
        views.SubmissionsAndPublicationsChart.as_view(),
        name="wjs_stats_submissions_and_publications",
    ),
    path(
        "editors-and-keywords-per-journal.tsv",
        views.EditorAndKeywordsPerJournalTSV.as_view(),
        name="wjs_stats_editors_and_keywords_per_journal_tsv",
    ),
    path(
        "editors-and-keywords/",
        views.EditorsAndKeywordsChar.as_view(),
        name="wjs_stats_editors_and_keywords",
    ),
]
