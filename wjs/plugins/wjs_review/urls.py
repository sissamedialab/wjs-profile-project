from django.urls import path
from django.views.generic import TemplateView

from .plugin_settings import MANAGER_URL
from .views import (
    ArticleDetails,
    EvaluateReviewRequest,
    InviteReviewer,
    ListArticles,
    ReviewDeclined,
    ReviewEnd,
    ReviewSubmit,
    SelectReviewer,
    UpdateState,
)

urlpatterns = [
    path("manager/", TemplateView.as_view(), name=MANAGER_URL),
    path("review/", ListArticles.as_view(), name="wjs_review_list"),
    path("update/<int:pk>/", UpdateState.as_view(), name="update_state"),
    path("select_reviewer/<int:pk>/", SelectReviewer.as_view(), name="wjs_select_reviewer"),
    path("invite_reviewer/<int:pk>/", InviteReviewer.as_view(), name="wjs_review_invite"),
    path("status/<int:pk>/", ArticleDetails.as_view(), name="wjs_article_details"),
    path("review/<int:assignment_id>/", ReviewSubmit.as_view(), name="wjs_review_review"),
    path("review/<int:assignment_id>/end/", ReviewEnd.as_view(), name="wjs_review_end"),
    path("review/<int:assignment_id>/evaluate/", EvaluateReviewRequest.as_view(), name="wjs_evaluate_review"),
    path(
        "review/<int:assignment_id>/evaluate/<str:token>/",
        EvaluateReviewRequest.as_view(),
        name="wjs_evaluate_review",
    ),
    path("review/<int:assignment_id>/declined/", ReviewDeclined.as_view(), name="wjs_declined_review"),
]
