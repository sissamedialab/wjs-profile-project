from django.urls import path
from django.views.generic import TemplateView

from .plugin_settings import MANAGER_URL
from .views import (
    ArticleDecision,
    ArticleDetails,
    EvaluateReviewRequest,
    InviteReviewer,
    ListArticles,
    Messages,
    MyMessages,
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
    path(
        "invite_reviewer/<int:pk>/",
        InviteReviewer.as_view(),
        name="wjs_review_invite",
    ),  # TODO: rename view name in the form predicate_object (e.g. wjs_invite_review) ?
    path("status/<int:pk>/", ArticleDetails.as_view(), name="wjs_article_details"),
    path("decision/<int:pk>/", ArticleDecision.as_view(), name="wjs_article_decision"),
    path("review/<int:assignment_id>/", ReviewSubmit.as_view(), name="wjs_review_review"),
    path("review/<int:assignment_id>/end/", ReviewEnd.as_view(), name="wjs_review_end"),
    path("review/<int:assignment_id>/evaluate/", EvaluateReviewRequest.as_view(), name="wjs_evaluate_review"),
    path(
        "review/<int:assignment_id>/evaluate/<str:token>/",
        EvaluateReviewRequest.as_view(),
        name="wjs_evaluate_review",
    ),
    path("review/<int:assignment_id>/declined/", ReviewDeclined.as_view(), name="wjs_declined_review"),
    path("my_messages", MyMessages.as_view(), name="wjs_my_messages"),
    path("messages/<int:article_id>/<int:recipient_id>", Messages.as_view(), name="wjs_article_messages"),
]
