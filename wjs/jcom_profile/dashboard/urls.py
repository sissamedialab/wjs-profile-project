from django.urls import path

from . import views

urlpatterns = [
    path("", views.RedirectDashboard.as_view(), name="core_dashboard"),
    path("active/", views.RedirectMyPages.as_view(), name="core_active_submissions"),
    path("active/filters/", views.RedirectMyPages.as_view(), name="core_submission_filter"),
    path("article/<str:article_id>/", views.RedirectArticleStatus.as_view(), name="core_dashboard_article"),
]
