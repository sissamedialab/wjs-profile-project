from django.urls import path

from .plugin_settings import MANAGER_URL
from .views import (
    ArticleDecision,
    ArticleDetails,
    ArticleMessages,
    ArticleRevisionUpdate,
    AuthorArchived,
    AuthorPending,
    EOArchived,
    EOMissingEditor,
    EOPending,
    EOProduction,
    EvaluateReviewRequest,
    InviteReviewer,
    ListArchivedArticles,
    ListArticles,
    Manager,
    MessageAttachmentDownloadView,
    ReviewDeclined,
    ReviewEnd,
    ReviewerArchived,
    ReviewerPending,
    ReviewSubmit,
    SelectReviewer,
    ToggleMessageReadByEOView,
    ToggleMessageReadView,
    UpdateState,
    UploadRevisionAuthorCoverLetterFile,
    WriteMessage,
)

urlpatterns = [
    path("manager/", Manager.as_view(), name=MANAGER_URL),
    path("review/", ListArticles.as_view(), name="wjs_review_list"),
    path("archived_papers/", ListArchivedArticles.as_view(), name="wjs_review_archived_papers"),
    path("eo_pending/", EOPending.as_view(), name="wjs_review_eo_pending"),
    path("eo_archived/", EOArchived.as_view(), name="wjs_review_eo_archived"),
    path("eo_production/", EOProduction.as_view(), name="wjs_review_eo_production"),
    path("eo_missing_editor/", EOMissingEditor.as_view(), name="wjs_review_eo_missing_editor"),
    path("author_pending/", AuthorPending.as_view(), name="wjs_review_author_pending"),
    path("author_archived/", AuthorArchived.as_view(), name="wjs_review_author_archived"),
    path("reviewer_pending/", ReviewerPending.as_view(), name="wjs_review_reviewer_pending"),
    path("reviewer_archived/", ReviewerArchived.as_view(), name="wjs_review_reviewer_archived"),
    path("update/<int:pk>/", UpdateState.as_view(), name="update_state"),
    path("select_reviewer/<int:pk>/", SelectReviewer.as_view(), name="wjs_select_reviewer"),
    path(
        "invite_reviewer/<int:pk>/",
        InviteReviewer.as_view(),
        name="wjs_invite_reviewer",
    ),
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
    path("article/<int:article_id>/revision/<int:revision_id>/", ArticleRevisionUpdate.as_view(), name="do_revisions"),
    path(
        "article/<int:article_id>/revision/<int:revision_id>/upload/",
        UploadRevisionAuthorCoverLetterFile.as_view(),
        name="wjs_upload_file",
    ),
    path("messages/<int:article_id>/", ArticleMessages.as_view(), name="wjs_article_messages"),
    path("messages/<int:article_id>/<int:recipient_id>/", WriteMessage.as_view(), name="wjs_message_write"),
    path(
        "messages/toggle_read_by_eo/<int:message_id>/",
        ToggleMessageReadByEOView.as_view(),
        name="wjs_message_toggle_read_by_eo",
    ),
    path(
        "messages/toggle_read/<int:message_id>/<int:recipient_id>",
        ToggleMessageReadView.as_view(),
        name="wjs_message_toggle_read",
    ),
    path(
        "messages/attachment/<int:message_id>/<int:attachment_id>",
        MessageAttachmentDownloadView.as_view(),
        name="wjs_message_download_attachment",
    ),
]
