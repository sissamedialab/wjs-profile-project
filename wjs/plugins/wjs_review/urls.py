from django.urls import path

from .plugin_settings import MANAGER_URL
from .views import (
    AdminOpensAppealView,
    ArticleAdminDecision,
    ArticleAdminDispatchAssignment,
    ArticleDecision,
    ArticleDetails,
    ArticleExtraInformationUpdateView,
    ArticleMessages,
    ArticleReminders,
    ArticleRevisionFileUpdate,
    ArticleRevisionUpdate,
    AssignEoToArticle,
    AuthorArchived,
    AuthorPending,
    AuthorWithdrawPreprint,
    DeselectReviewer,
    DirectorArchived,
    DirectorPending,
    DirectorWorkOnAPaper,
    DirectorWorkOnIssue,
    DownloadAnythingDROPME,
    EditorArchived,
    EditorAssignsThemselvesAsReviewer,
    EditorDeclineAssignmentView,
    EditorPending,
    EOArchived,
    EOPending,
    EOProduction,
    EOWorkOnAPaper,
    EOWorkOnIssue,
    EvaluateReviewRequest,
    ForwardMessage,
    InviteReviewer,
    JournalEditorsView,
    Manager,
    MessageAttachmentDownloadView,
    PostponeRevisionRequestDueDate,
    ReviewDeclined,
    ReviewEnd,
    ReviewerArchived,
    ReviewerPending,
    ReviewSubmit,
    SelectReviewer,
    SupervisorAssignEditor,
    ToggleMessageReadByEOView,
    ToggleMessageReadView,
    UpdateReviewerDueDate,
    UpdateState,
    UploadRevisionAuthorCoverLetterFile,
    WriteMessage,
)
from .views__production import (  # noqa F401
    AuthorSendsCorrectionsView,
    BeginPublicationView,
    CreateSupplementaryFileView,
    DeleteSupplementaryFileView,
    DownloadRevisionFiles,
    EOSendBackToTypesetterView,
    FinishPublicationView,
    GalleyGenerationView,
    ListAnnotatedFilesView,
    ReadyForProofreadingView,
    ReadyForPublicationView,
    TogglePublishableFlagView,
    TypesetterArchived,
    TypesetterPending,
    TypesetterTakeInCharge,
    TypesetterUploadFiles,
    TypesetterWorkingOn,
    UpdateSectionOrder,
    WriteToAuWithModeration,
    WriteToTyp,
)
from .views__visibility import EditUserPermissions

urlpatterns = [
    path("manager/", Manager.as_view(), name=MANAGER_URL),
    path("editor/pending/", EditorPending.as_view(), name="wjs_review_list"),
    path("editor/archived/", EditorArchived.as_view(), name="wjs_review_archived_papers"),
    path("eo/pending/", EOPending.as_view(), name="wjs_review_eo_pending"),
    path("eo/archived/", EOArchived.as_view(), name="wjs_review_eo_archived"),
    path("eo/production/", EOProduction.as_view(), name="wjs_review_eo_production"),
    path("eo/workon/", EOWorkOnAPaper.as_view(), name="wjs_review_eo_workon"),
    path("eo/issues/", EOWorkOnIssue.as_view(), name="wjs_review_eo_issues_list"),
    path("director/pending/", DirectorPending.as_view(), name="wjs_review_director_pending"),
    path("director/archived/", DirectorArchived.as_view(), name="wjs_review_director_archived"),
    path("director/issues/", DirectorWorkOnIssue.as_view(), name="wjs_review_director_issues_list"),
    path("director/workon/", DirectorWorkOnAPaper.as_view(), name="wjs_review_director_workon"),
    path("author/pending/", AuthorPending.as_view(), name="wjs_review_author_pending"),
    path("author/archived/", AuthorArchived.as_view(), name="wjs_review_author_archived"),
    path("author/rfp/<int:pk>/", ReadyForPublicationView.as_view(), name="wjs_review_rfp"),
    path("reviewer/pending/", ReviewerPending.as_view(), name="wjs_review_reviewer_pending"),
    path("reviewer/archived/", ReviewerArchived.as_view(), name="wjs_review_reviewer_archived"),
    path("typesetter/pending/", TypesetterPending.as_view(), name="wjs_review_typesetter_pending"),
    path("typesetter/workingon/", TypesetterWorkingOn.as_view(), name="wjs_review_typesetter_workingon"),
    path("typesetter/archived/", TypesetterArchived.as_view(), name="wjs_review_typesetter_archived"),
    # Both authors and typs can set a paper ready for publication; the following is just an alias:
    path("typesetter/rfp/<int:pk>/", ReadyForPublicationView.as_view(), name="wjs_review_rfp"),
    path("update/<int:pk>/", UpdateState.as_view(), name="update_state"),
    path("issues/<int:pk>/sections/order/", UpdateSectionOrder.as_view(), name="wjs_order_sections"),
    path("assign_eo/<int:pk>/", AssignEoToArticle.as_view(), name="wjs_assign_eo"),
    path(
        "editor_assigns_themselves_as_reviewer/<int:pk>/",
        EditorAssignsThemselvesAsReviewer.as_view(),
        name="wjs_editor_assigns_themselves_as_reviewer",
    ),
    path("select_reviewer/<int:pk>/", SelectReviewer.as_view(), name="wjs_select_reviewer"),
    path(
        "deselect_reviewer/<int:pk>/",
        DeselectReviewer.as_view(),
        name="wjs_deselect_reviewer",
    ),
    path(
        "assigns_editor/<int:pk>/",
        SupervisorAssignEditor.as_view(),
        name="wjs_assigns_editor",
    ),
    path("postpone_duedate/<int:pk>/", UpdateReviewerDueDate.as_view(), name="wjs_postpone_reviewer_due_date"),
    path(
        "invite_reviewer/<int:pk>/",
        InviteReviewer.as_view(),
        name="wjs_invite_reviewer",
    ),
    path(
        "postpone_revision_request/<int:pk>/",
        PostponeRevisionRequestDueDate.as_view(),
        name="wjs_postpone_revision_request",
    ),
    path(
        "invite_reviewer/<int:pk>/<int:prophy_account_id>/",
        InviteReviewer.as_view(),
        name="wjs_invite_reviewer_prophy",
    ),
    path("status/<int:pk>/", ArticleDetails.as_view(), name="wjs_article_details"),
    path("additional_info/<int:pk>/", ArticleExtraInformationUpdateView.as_view(), name="wjs_article_additional_info"),
    path("decision/<int:pk>/", ArticleDecision.as_view(), name="wjs_article_decision"),
    path("admin_decision/<int:pk>/", ArticleAdminDecision.as_view(), name="wjs_article_admin_decision"),
    path(
        "dispatch_assignment/<int:pk>/",
        ArticleAdminDispatchAssignment.as_view(),
        name="wjs_article_dispatch_assignment",
    ),
    path("decision/unassign/<int:pk>/", EditorDeclineAssignmentView.as_view(), name="wjs_unassign_assignment"),
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
        "article/<int:article_id>/revision/<int:revision_id>/files/<str:file_type>/",
        ArticleRevisionFileUpdate.as_view(),
        name="revisions_use_files",
    ),
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
        "messages/toggle_read/<int:message_id>/<int:recipient_id>/",
        ToggleMessageReadView.as_view(),
        name="wjs_message_toggle_read",
    ),
    path(
        "messages/attachment/<int:message_id>/<int:attachment_id>/",
        MessageAttachmentDownloadView.as_view(),
        name="wjs_message_download_attachment",
    ),
    path(
        "messages/writetotyp/<int:pk>/",
        WriteToTyp.as_view(),
        name="wjs_message_write_to_typ",
    ),
    path(
        "messages/writetoau/<int:pk>/",
        WriteToAuWithModeration.as_view(),
        name="wjs_message_write_to_auwm",
    ),
    path(
        "messages/forward/<int:original_message_pk>/",
        ForwardMessage.as_view(),
        name="wjs_message_forward",
    ),
    # TODO: rethink naming of views.
    # For the messages we have messages/..., but for the reminders it is article/ID/reminders
    path("article/<int:article_id>/reminders/", ArticleReminders.as_view(), name="wjs_article_reminders"),
    path("journal_editors/", JournalEditorsView.as_view(), name="wjs_journal_editors"),
    path("upload_files/<int:pk>/", TypesetterUploadFiles.as_view(), name="wjs_typesetter_upload_files"),
    path("download_revision_files/<int:pk>/", DownloadRevisionFiles.as_view(), name="download_revision_files"),
    path("ready_for_proofreading/<int:pk>/", ReadyForProofreadingView.as_view(), name="wjs_ready_for_proofreading"),
    path(
        "create_supplementary_file/<int:article_id>/",
        CreateSupplementaryFileView.as_view(),
        name="create_supplementary_file",
    ),
    path(
        "delete_supplementary_file/<int:file_id>/",
        DeleteSupplementaryFileView.as_view(),
        name="delete_supplementary_file",
    ),
    path("annotated_files/<int:pk>/", ListAnnotatedFilesView.as_view(), name="wjs_list_annotated_files"),
    path("send_corrections/<int:pk>/", AuthorSendsCorrectionsView.as_view(), name="wjs_author_sends_corrections"),
    path("paper_publishable/<int:pk>/", TogglePublishableFlagView.as_view(), name="wjs_toggle_publishable"),
    path("galley_generation/<int:pk>/", GalleyGenerationView.as_view(), name="wjs_typesetter_galley_generation"),
    path(
        "article/<int:pk>/permissions/<int:user_id>/",
        EditUserPermissions.as_view(),
        name="wjs_assign_permission",
    ),
    path("begin_publication/<int:pk>/", BeginPublicationView.as_view(), name="wjs_review_begin_publication"),
    path("finish_publication/<int:pk>/", FinishPublicationView.as_view(), name="wjs_review_finish_publication"),
    path(
        "send_back_to_typesetter/<int:pk>/",
        EOSendBackToTypesetterView.as_view(),
        name="wjs_send_back_to_typ",
    ),
    path("take_in_charge/<int:pk>/", TypesetterTakeInCharge.as_view(), name="wjs_typ_take_in_charge"),
    path(
        "download_anything/<int:article_id>/<int:file_id>",
        DownloadAnythingDROPME.as_view(),
        name="download_anything_dropme",
    ),
    path("open_appeal/<int:pk>/", AdminOpensAppealView.as_view(), name="wjs_open_appeal"),
    path("withdraw/<int:pk>/", AuthorWithdrawPreprint.as_view(), name="wjs_author_withdraw_preprint"),
]
