from django.urls import path
from django.views.generic import TemplateView

from .plugin_settings import MANAGER_URL
from .views import ListArticles, SelectReviewer, UpdateState

urlpatterns = [
    path("manager/", TemplateView.as_view(), name=MANAGER_URL),
    path("review/", ListArticles.as_view(), name="wjs_review_list"),
    path("update/<int:pk>/", UpdateState.as_view(), name="update_state"),
    path("select_reviewer/<int:pk>/", SelectReviewer.as_view(), name="wjs_select_reviewer"),
]
