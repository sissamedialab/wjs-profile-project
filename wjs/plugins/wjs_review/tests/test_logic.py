import datetime
from typing import Callable, List, Optional
from unittest import mock
from unittest.mock import patch

import freezegun
import pytest
from core.models import Account
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.exceptions import ValidationError
from django.forms import models as model_forms
from django.http import HttpRequest
from django.urls import reverse
from django.utils.timezone import localtime, now
from faker import Faker
from journal import models as journal_models
from plugins.wjs_review.logic__production import MetadataFromTeX, reunite_divided_kwds
from review import models as review_models
from submission import models as submission_models
from submission.models import Article, ArticleAuthorOrder, Keyword
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import (
    generate_token,
    get_eo_user,
    render_template_from_setting,
)

from .. import permissions
from ..communication_utils import get_system_user
from ..events.handlers import (
    dispatch_eo_assignment,
    restart_review_process_after_revision_submission,
    sync_article_articleworkflow,
)
from ..forms import (
    AssignEoForm,
    EditorRevisionRequestEditForm,
    MessageForm,
    OpenAppealForm,
    SupervisorAssignEditorForm,
    WithdrawPreprintForm,
)
from ..logic import (
    AdminActions,
    AssignToEditor,
    AssignToReviewer,
    AuthorHandleRevision,
    CreateReviewRound,
    DeselectReviewer,
    EvaluateReview,
    HandleDecision,
    HandleEditorDeclinesAssignment,
    InviteReviewer,
    PostponeReviewerDueDate,
    PostponeRevisionRequestDueDate,
    SubmitReview,
)
from ..logic__visibility import PermissionChecker
from ..models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    Message,
    MessageRecipients,
    PastEditorAssignment,
    PermissionAssignment,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..plugin_settings import STAGE
from ..reminders.settings import (
    EditorShouldSelectReviewerReminderManager,
    ReviewerShouldEvaluateAssignmentReminderManager,
    ReviewerShouldWriteReviewReminderManager,
)
from ..templatetags.wjs_articles import last_eo_note, last_user_note
from ..utils import get_report_form
from ..views import ArticleRevisionUpdate
from .test_helpers import (
    _create_review_assignment,
    _submit_review,
    jcom_report_form_data,
    raw,
)

fake_factory = Faker()


@pytest.mark.django_db
def test_articleworkflow__get_sorted_review_assignments(
    fake_request: HttpRequest,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    assigned_article: submission_models.Article,
    review_settings,
    review_form: review_models.ReviewForm,
) -> None:
    """Test the ordering of review-assignments.

    RAs are first grouped by type: all the completed ones, then all the accepted ones, etc.
    Then, RAs of each group are sorted wrt the interesting date for that group:
    - completed assignments are sorted using their "competed" date, ignoring the other dates
    - accepted assignments are sorted using their "accepted" date,
    - etc.

    Here we create 3 assignments (one in each state: completed, accepted (only), and declined)
    for two reviewers, for a total of 6 assignments.

    We change the dates of the two assignments in each state,
    and verify that the sorting function behaves correctly.
    """
    today = now().date()
    reviewer_1 = create_jcom_user("reviewer_1")
    reviewer_2 = create_jcom_user("reviewer_2")
    for reviewer in [reviewer_1, reviewer_2]:
        fake_request.user = reviewer
        submitted_review = _create_review_assignment(
            fake_request=fake_request,
            reviewer_user=reviewer,
            assigned_article=assigned_article,
        )
        _submit_review(submitted_review, fake_request)
        if reviewer == reviewer_2:
            submitted_review.date_complete = today + datetime.timedelta(days=3)
            submitted_review.save()

        accepted_review = _create_review_assignment(
            fake_request=fake_request,
            reviewer_user=reviewer,
            assigned_article=assigned_article,
        )
        if reviewer == reviewer_1:
            accepted_review.date_accepted = today + datetime.timedelta(days=3)
        else:
            accepted_review.date_accepted = today
        accepted_review.save()

        declined_review = _create_review_assignment(
            fake_request=fake_request,
            reviewer_user=reviewer,
            assigned_article=assigned_article,
        )
        if reviewer == reviewer_2:
            declined_review.date_declined = today + datetime.timedelta(days=3)
        else:
            declined_review.date_declined = today
        declined_review.date_accepted = None
        declined_review.is_complete = True
        declined_review.save()
    sorted_review_assignments = assigned_article.articleworkflow._get_sorted_review_assignments(
        assigned_article.current_review_round_object()
    )
    # Declined assignments:
    assert sorted_review_assignments.filter(date_declined__isnull=False).first().reviewer.jcomprofile == reviewer_2
    # Completed assignments:
    assert sorted_review_assignments.filter(date_complete__isnull=False).first().reviewer.jcomprofile == reviewer_2
    # Accepted assignemts:
    # warning: do not use date_accepted only, becuase it is also set (i.e. it is not-null)
    #          for completed-assignments
    assert (
        sorted_review_assignments.filter(
            date_accepted__isnull=False,
            date_complete__isnull=True,
        )
        .first()
        .reviewer.jcomprofile
        == reviewer_1
    )


@pytest.mark.django_db
def test_low_level_dispatch_eo_assign(article: submission_models.Article, eo_user: JCOMProfile) -> None:
    """Dispatch assignment to EO."""
    assert not article.articleworkflow.eo_in_charge
    dispatch_eo_assignment(article=article)
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.eo_in_charge


@pytest.mark.django_db
def test_assign_to_eo(article: submission_models.Article, eo_user: JCOMProfile) -> None:
    """Rung post-submission event handler to assign to EO."""
    article.stage = STAGE
    article.save()
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION
    article.articleworkflow.save()
    assert not article.articleworkflow.eo_in_charge
    sync_article_articleworkflow(article=article)
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.eo_in_charge


@pytest.mark.django_db
def test_manual_assign_eo_form(
    assigned_article: submission_models.Article,
    eo_user: JCOMProfile,
    fake_request: HttpRequest,
    eo_group: Group,
    create_jcom_user: Callable,
):
    """
    Test the form to manually assign an EO to an article.

        :param assigned_article: Article to assign EO to
        :param eo_user: Executing EO User
        :param fake_request: Fake request object
        :param eo_group: EO Group object
        :param create_jcom_user: function to create users
    """
    second_eo = create_jcom_user("second_eo")
    second_eo.groups.add(eo_group)
    Message.objects.first()
    assert not assigned_article.articleworkflow.eo_in_charge
    fake_request.user = eo_user.janeway_account
    form_data = {
        "eo_in_charge": second_eo.janeway_account,
    }
    form = AssignEoForm(data=form_data, instance=assigned_article.articleworkflow, user=eo_user, request=fake_request)
    assert form.is_valid()
    workflow = form.save()
    assert workflow.eo_in_charge == second_eo.janeway_account
    assigned_article.articleworkflow.refresh_from_db()
    assert assigned_article.articleworkflow.eo_in_charge == second_eo.janeway_account
    assert Message.objects.count() == 1
    msg = Message.objects.first()
    assert msg.actor == eo_user.janeway_account
    assert msg.recipients.filter(pk=second_eo.janeway_account.pk).exists()


@pytest.mark.parametrize("current_user_editor", (True, False))
@pytest.mark.django_db
def test_assign_to_editor(
    review_settings,
    fake_request: HttpRequest,
    director: JCOMProfile,
    section_editor: JCOMProfile,
    article: submission_models.Article,
    current_user_editor: bool,
):
    """An editor can be assigned to an article and objects states are updated."""
    # Note that having request.user == editor means that the same person that is doing the action
    # is assignining himself as editor, which should not happen.
    # See specs#1644 and related MR for a discussion.
    if current_user_editor:
        fake_request.user = section_editor.janeway_account
    else:
        fake_request.user = director.janeway_account
    article.stage = submission_models.STAGE_UNSUBMITTED
    article.save()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()

    service = AssignToEditor(
        article=article,
        editor=section_editor.janeway_account,
        request=fake_request,
        first_assignment=True,
    )
    assert WjsEditorAssignment.objects.get_all(article).count() == 0
    assert article.reviewround_set.count() == 0

    assignment = service.run()

    workflow = assignment.article.articleworkflow
    assert workflow.article == article
    article.refresh_from_db()
    assert article.stage == submission_models.STAGE_ASSIGNED
    assert WjsEditorAssignment.objects.get_all(article).count() == 1
    assert WjsEditorAssignment.objects.get_current(article).editor == section_editor.janeway_account
    assert article.reviewround_set.count() == 1
    assert article.reviewround_set.filter(round_number=1).count() == 1
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    # Check messages
    assert Message.objects.count() == 1
    message_to_editor = Message.objects.first()
    editor_assignment_message = render_template_from_setting(
        setting_group_name="email",
        setting_name="editor_assignment",
        journal=article.journal,
        request=fake_request,
        context={
            "article": article,
            "request": fake_request,
            "editor": section_editor.janeway_account,
            "default_editor_assign_reviewer_days": get_setting(
                setting_group_name="wjs_review",
                setting_name="default_editor_assign_reviewer_days",
                journal=article.journal,
            ).processed_value,
        },
        template_is_setting=True,
    )
    assert message_to_editor.body == editor_assignment_message
    assert message_to_editor.message_type == Message.MessageTypes.SYSTEM
    assert list(message_to_editor.recipients.all()) == [section_editor.janeway_account]
    if current_user_editor:
        assert message_to_editor.actor == section_editor.janeway_account
    else:
        assert message_to_editor.actor == director.janeway_account


@pytest.mark.parametrize(
    "user_with_role,si,initial_state,expected",
    (
        ("eo", True, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, True),
        ("eo", True, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, True),
        ("eo", True, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, True),
        ("eo", False, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, True),
        ("eo", False, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, True),
        ("eo", False, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, True),
        ("director", True, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, True),
        ("director", True, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, True),
        ("director", True, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, True),
        ("director", False, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, True),
        ("director", False, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, True),
        ("director", False, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, True),
        ("section-editor", True, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, True),
        ("section-editor", True, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, True),
        ("section-editor", True, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, True),
        ("section-editor", False, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, False),
        ("section-editor", False, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, False),
        ("section-editor", False, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, False),
        ("reviewer", True, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, False),
        ("reviewer", True, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, False),
        ("reviewer", True, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, False),
        ("reviewer", False, ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION, False),
        ("reviewer", False, ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, False),
        ("reviewer", False, ArticleWorkflow.ReviewStates.EDITOR_SELECTED, False),
    ),
    indirect=["user_with_role"],
)
@pytest.mark.django_db
def test_user_is_article_supervisor(
    review_settings,
    fake_request: HttpRequest,
    article: submission_models.Article,
    section_editor: JCOMProfile,
    special_issue: journal_models.Issue,
    user_with_role,
    si,
    initial_state,
    expected,
):
    """A user is article supervisor only when EO/director or special issue editor."""
    fake_request.user = user_with_role.janeway_account
    article.stage = submission_models.STAGE_UNSUBMITTED
    article.save()
    article.articleworkflow.state = initial_state
    article.articleworkflow.save()
    if si:
        special_issue.articles.add(article)
        special_issue.managing_editors.add(user_with_role)
        article.refresh_from_db()

    if expected:
        assert permissions.is_article_supervisor(article.articleworkflow, user_with_role)
    else:
        assert not permissions.is_article_supervisor(article.articleworkflow, user_with_role)


@pytest.mark.django_db
def test_assign_to_non_editor(
    fake_request: HttpRequest,
    reviewer: JCOMProfile,
    article: submission_models.Article,
):
    """A non editor cannot be assigned to an article and objects states are unchanged."""
    fake_request.user = reviewer.janeway_account
    article.stage = "Unsubmitted"
    article.save()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()

    service = AssignToEditor(
        article=article,
        editor=reviewer.janeway_account,
        request=fake_request,
    )
    assert WjsEditorAssignment.objects.get_all(article).count() == 0

    with pytest.raises(ValueError, match="Invalid state transition"):
        service.run()
    article.refresh_from_db()
    assert WjsEditorAssignment.objects.get_all(article).count() == 0
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    # Check messages
    assert Message.objects.count() == 0


@pytest.mark.django_db
def test_assign_to_reviewer_hijacked(
    review_settings,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """
    A reviewer can be assigned to an article and objects states are updated.

    When the user is hijacked, an additional notification is sent to the hijacked user.
    """
    fake_request.user = section_editor.janeway_account
    fake_request.user.is_hijacked = True
    setattr(fake_request, "session", {"hijack_history": [eo_user.pk]})  # noqa: B010

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": now().date() + datetime.timedelta(days=7),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )
    service.run()
    assigned_article.refresh_from_db()
    assert len(mail.outbox) == 2
    user_emails = [m for m in mail.outbox if m.to[0] == normal_user.email]
    editor_emails = [m for m in mail.outbox if m.to[0] == section_editor.email]

    review_assignment_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_assignment",
        journal=assigned_article.journal,
        request=fake_request,
        context={"article": assigned_article, "reviewer": normal_user.janeway_account},
        template_is_setting=True,
    )

    hijack_notification_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="hijack_notification_subject",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "journal": assigned_article.journal,
            "original_subject": review_assignment_subject,
        },
        template_is_setting=True,
    )

    assert len(user_emails) == 1
    assert len(editor_emails) == 1
    assert review_assignment_subject in user_emails[0].subject
    assert assigned_article.journal.code in user_emails[0].subject
    assert hijack_notification_subject in editor_emails[0].subject


@pytest.mark.django_db
def test_editor_assigns_themselves_as_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """An editor assigns themselves as reviewer of an article."""
    fake_request.user = section_editor.janeway_account
    _now = localtime(now())

    acceptance_due_date = _now.date() + datetime.timedelta(days=7)
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=section_editor.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": acceptance_due_date.strftime("%Y-%m-%d"),
            "message": "random message",
        },
        request=fake_request,
    )
    assert section_editor.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    assert Message.objects.count() == 0
    assignment = service.run()
    assigned_article.refresh_from_db()

    context = service._get_message_context()
    assert context["article"] == assigned_article
    assert context["journal"] == assigned_article.journal
    assert context["request"] == fake_request
    assert context["user_message_content"] == "random message"
    assert context["review_assignment"] == assignment
    assert context["acceptance_due_date"] == acceptance_due_date
    assert context["reviewer"] == section_editor.janeway_account
    assert not context["major_revision"]
    assert not context["minor_revision"]
    assert not context["already_reviewed"]
    assert isinstance(context["acceptance_due_date"], datetime.date)

    assert section_editor.janeway_account in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Under Review"
    assert assigned_article.reviewassignment_set.count() == 1
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    # This is "delicate": the presence or absence of ReviewAssignments does not change the article's state
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert assignment.reviewer == section_editor.janeway_account
    assert assignment.editor == section_editor.janeway_account
    assert localtime(assignment.date_accepted).date() == _now.date()

    context = {"article": assigned_article, "review_assignment": assignment}
    message_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="wjs_editor_i_will_review_message_subject",
        journal=assigned_article.journal,
        request=fake_request,
        context=context,
        template_is_setting=True,
    )
    message_body = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="wjs_editor_i_will_review_message_body",
        journal=assigned_article.journal,
        request=fake_request,
        context=context,
        template_is_setting=True,
    )
    # Check message
    assert Message.objects.count() == 1
    message = Message.objects.first()
    assert message.subject == message_subject
    # Check that the message body passed via form is ignored, and that the setting's text is used
    assert message.body == message_body
    assert message.message_type == Message.MessageTypes.SYSTEM
    assert message.actor == section_editor.janeway_account
    assert list(message.recipients.all()) == [section_editor.janeway_account]


@pytest.mark.django_db
def test_assign_to_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """A reviewer can be assigned to an article and objects states are updated."""
    fake_request.user = section_editor.janeway_account

    acceptance_due_date = now().date() + datetime.timedelta(days=7)
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": acceptance_due_date.strftime("%Y-%m-%d"),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    )
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    assignment = service.run()
    assigned_article.refresh_from_db()

    context = service._get_message_context()
    assert context["article"] == assigned_article
    assert context["journal"] == assigned_article.journal
    assert context["request"] == fake_request
    assert context["user_message_content"] == "random message"
    assert context["review_assignment"] == assignment
    assert context["acceptance_due_date"] == acceptance_due_date
    assert context["reviewer"] == normal_user.janeway_account
    assert not context["major_revision"]
    assert not context["minor_revision"]
    assert not context["already_reviewed"]
    assert isinstance(context["acceptance_due_date"], datetime.date)

    assert normal_user.janeway_account in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Under Review"
    assert assigned_article.reviewassignment_set.count() == 1
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    # This is "delicate": the presence or absence of ReviewAssignments does not change the article's state
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert assignment.reviewer == normal_user.janeway_account
    assert assignment.editor == section_editor.janeway_account
    last_version = assigned_article.articleworkflow.get_review_versions(normal_user.janeway_account)[0]
    assert PermissionAssignment.objects.filter(
        user=normal_user.janeway_account,
        content_type_id=ContentType.objects.get_for_model(last_version.cover_letter.object).pk,
        object_id=last_version.cover_letter.object.pk,
        permission=PermissionAssignment.PermissionType.NO_NAMES,
        permission_secondary=PermissionAssignment.BinaryPermissionType.DENY,
    ).exists()

    review_assignment_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_assignment",
        journal=assigned_article.journal,
        request=fake_request,
        context={"article": assigned_article, "reviewer": normal_user.janeway_account},
        template_is_setting=True,
    )
    url = reverse(
        "wjs_evaluate_review",
        kwargs={"assignment_id": assignment.pk},
    )
    acceptance_url = f"{url}?access_code={assigned_article.reviewassignment_set.first().access_code}"
    # 1 notification to the reviewer (by AssignToReviewer)
    # Check emails
    assert len(mail.outbox) == 1
    emails = [m for m in mail.outbox if m.to[0] == normal_user.email]
    assert len(emails) == 1
    assert review_assignment_subject in emails[0].subject
    assert assigned_article.journal.code in emails[0].subject
    assert "You have been invited" not in emails[0].body
    assert acceptance_url in emails[0].body.replace("\n", "")  # ATM, URL is broken by newline... why???
    assert "random message" in emails[0].body
    # Check messages
    assert Message.objects.count() == 1
    message_to_invited_user = Message.objects.first()
    assert message_to_invited_user.subject == review_assignment_subject
    assert "random message" in message_to_invited_user.body
    assert acceptance_url in message_to_invited_user.body
    assert "You have been invited" not in message_to_invited_user.body
    assert message_to_invited_user.message_type == Message.MessageTypes.SYSTEM
    assert message_to_invited_user.actor == section_editor.janeway_account
    assert list(message_to_invited_user.recipients.all()) == [normal_user.janeway_account]


@pytest.mark.django_db
def test_cannot_assign_to_reviewer_if_revision_requested(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """A reviewer cannot be assigned if a revision request is in progress."""
    fake_request.user = section_editor.janeway_account
    form_data = {
        "decision": ArticleWorkflow.Decisions.MINOR_REVISION,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": now().date() + datetime.timedelta(days=7),
            "message": "random message",
        },
        request=fake_request,
    )
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.TO_BE_REVISED

    with pytest.raises(ValueError, match="Transition conditions not met"):
        service.run()

    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.TO_BE_REVISED
    # Prepare templates
    revision_request_message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_request_revisions",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "minor_revision": form_data["decision"] == ArticleWorkflow.Decisions.MINOR_REVISION,
            "major_revision": form_data["decision"] == ArticleWorkflow.Decisions.MAJOR_REVISION,
        },
        template_is_setting=True,
    )
    revision = EditorRevisionRequest.objects.get(
        article=assigned_article,
        review_round=assigned_article.reviewround_set.get(),
    )
    revision_request_message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="request_revisions",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "article": assigned_article,
            "request": None,
            "revision": revision,
            "decision": form_data["decision"],
            "user_message_content": form_data["decision_editor_report"],
            "withdraw_notice": form_data["withdraw_notice"],
            "skip": False,
            "minor_revision": form_data["decision"] == ArticleWorkflow.Decisions.MINOR_REVISION,
            "major_revision": form_data["decision"] == ArticleWorkflow.Decisions.MAJOR_REVISION,
        },
        template_is_setting=True,
    )
    # Try to avoid test-rot and simplify comparison:
    revision_request_message_body = raw(revision_request_message_body)
    # Check message
    assert Message.objects.count() == 1
    message_to_correspondence_author = Message.objects.get()
    assert message_to_correspondence_author.subject == revision_request_message_subject
    assert revision_request_message_body in raw(message_to_correspondence_author.body)
    assert message_to_correspondence_author.message_type == Message.MessageTypes.SYSTEM
    assert message_to_correspondence_author.actor == section_editor.janeway_account
    assert list(message_to_correspondence_author.recipients.all()) == [assigned_article.correspondence_author]
    # Check email
    assert len(mail.outbox) == 1
    mail_to_correspondence_author = mail.outbox[0]
    assert revision_request_message_subject in mail_to_correspondence_author.subject
    # If message must be split, check that the first part is in the email body
    if Message.SPLIT_MARKER in revision_request_message_body:
        assert revision_request_message_body.partition(Message.SPLIT_MARKER)[0] in raw(
            mail_to_correspondence_author.body
        )
    else:
        assert revision_request_message_body in raw(mail_to_correspondence_author.body)
    assert (
        mail_to_correspondence_author.from_email
        == get_setting(
            "general",
            "from_address",
            assigned_article.journal,
        ).processed_value
    )
    assert mail_to_correspondence_author.from_email != section_editor.email
    assert list(mail_to_correspondence_author.recipients()) == [assigned_article.correspondence_author.email]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "revision_type,has_previous_assignment",
    (
        (ArticleWorkflow.Decisions.MINOR_REVISION, False),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, True),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, True),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, True),
    ),
)
def test_assign_to_reviewer_after_revision(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_settings,
    revision_type: str,
    has_previous_assignment: bool,
):
    """
    AssignToReviewer context contains flags for each revision type and customized based on reviewer history.

    For technical revision the review assignment is not considered because it does not trigger a new
     review round and the generated context refers to the same review round.
    """
    fake_request.user = section_editor.janeway_account
    form_data = {
        "decision": revision_type,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    if has_previous_assignment:
        review_assignment = _create_review_assignment(
            fake_request=fake_request,
            reviewer_user=normal_user,
            assigned_article=assigned_article,
        )
        # fake a "completed" review assignment
        review_assignment.date_accepted = localtime(now())
        review_assignment.is_complete = True
        review_assignment.save()

    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()
    revision_request = EditorRevisionRequest.objects.get(article=assigned_article)
    revision_request.date_completed = now()
    revision_request.save()
    restart_review_process_after_revision_submission(revision=revision_request)

    acceptance_due_date = localtime(now()).date() + datetime.timedelta(days=7)
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": acceptance_due_date,
            "message": "random message",
        },
        request=fake_request,
    )
    assignment = service.run()
    context = service._get_message_context()

    if revision_type == ArticleWorkflow.Decisions.MINOR_REVISION:
        assert context["minor_revision"]
        assert not context["major_revision"]
    elif revision_type == ArticleWorkflow.Decisions.MAJOR_REVISION:
        assert not context["minor_revision"]
        assert context["major_revision"]
    elif revision_type == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assert not context["minor_revision"]
        assert not context["major_revision"]
    assert context["article"] == assigned_article
    assert context["journal"] == assigned_article.journal
    assert context["request"] == fake_request
    assert context["user_message_content"] == "random message"
    assert context["review_assignment"] == assignment
    assert context["acceptance_due_date"] == acceptance_due_date
    assert context["reviewer"] == normal_user.janeway_account
    if revision_type == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        already_reviewed = False
    else:
        already_reviewed = has_previous_assignment
    assert context["already_reviewed"] == already_reviewed


@pytest.mark.django_db
def test_assign_to_reviewer_delete_editor_reminder(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_settings,
):
    """
    EDMD* reminders are deleted when the editor assigns a new reviewer.
    """
    fake_request.user = section_editor.janeway_account
    review_assignment = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=normal_user,
        assigned_article=assigned_article,
    )
    _submit_review(review_assignment, fake_request)

    # We want to test that we have exactly one reminder per type. get() ensure against multiple reminder count per code
    assert Reminder.objects.all().count() == 3
    assert Reminder.objects.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_1)
    assert Reminder.objects.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_2)
    assert Reminder.objects.get(code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3)

    acceptance_due_date = localtime(now()).date() + datetime.timedelta(days=7)
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": acceptance_due_date,
            "message": "random message",
        },
        request=fake_request,
    )
    service.run()

    # We want to test that we have exactly one reminder per type. get() ensure against multiple reminder count per code
    assert Reminder.objects.all().count() == 3
    assert Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1)
    assert Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_2)
    assert Reminder.objects.get(code=Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3)


@pytest.mark.django_db
def test_assign_to_reviewer_fails_no_form(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """A reviewer cannot be assigned if the review form is not assigned."""
    fake_request.user = section_editor.janeway_account

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=normal_user.janeway_account,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": now().date() + datetime.timedelta(days=7),
            "message": "random message",
        },
        request=fake_request,
    )
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Assigned"
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    with pytest.raises(ValueError, match="Cannot assign review"):
        service.run()

    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Assigned"
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert len(mail.outbox) == 0
    # Check messages
    assert Message.objects.count() == 0


@pytest.mark.django_db
def test_assign_to_reviewer_no_editor(
    fake_request: HttpRequest,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """A reviewer cannot be assigned if the requestor is not an editor."""
    fake_request.user = normal_user.janeway_account

    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=normal_user.janeway_account,
        editor=normal_user.janeway_account,
        form_data={
            "acceptance_due_date": now().date() + datetime.timedelta(days=7),
            "message": "random message",
        },
        request=fake_request,
    )
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("editor")
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert service.check_editor_conditions(assigned_article.articleworkflow, normal_user.janeway_account) is False
    assert service.check_reviewer_conditions(assigned_article.articleworkflow, normal_user.janeway_account) is True

    with pytest.raises(ValueError, match="Transition conditions not met"):
        service.run()

    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("editor")
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Assigned"
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert len(mail.outbox) == 0
    # Check messages
    assert Message.objects.count() == 0


@pytest.mark.django_db
def test_assign_to_reviewer_author(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """A reviewer cannot be assigned if the reviewer is one of the article authors."""
    fake_request.user = section_editor.janeway_account

    author = assigned_article.authors.first()
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=author,
        editor=section_editor.janeway_account,
        form_data={
            "acceptance_due_date": now().date() + datetime.timedelta(days=7),
            "message": "random message",
        },
        request=fake_request,
    )
    assert author not in assigned_article.journal.users_with_role("reviewer")
    assert section_editor.janeway_account in assigned_article.journal.users_with_role("section-editor")
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert service.check_editor_conditions(assigned_article.articleworkflow, section_editor.janeway_account) is True
    assert service.check_reviewer_conditions(assigned_article.articleworkflow, author) is False

    with pytest.raises(ValueError, match="Transition conditions not met"):
        service.run()

    assert author not in assigned_article.journal.users_with_role("reviewer")
    assert section_editor.janeway_account in assigned_article.journal.users_with_role("section-editor")
    assert assigned_article.stage == "Assigned"
    assert assigned_article.reviewassignment_set.count() == 0
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert len(mail.outbox) == 0
    # Check messages
    assert Message.objects.count() == 0


@pytest.mark.django_db
def test_invite_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """A user can be invited and a user a review assignment must be created."""
    fake_request.user = section_editor.janeway_account

    user_data = {
        "first_name": fake_factory.first_name(),
        "last_name": fake_factory.last_name(),
        "suffix": fake_factory.suffix(),
        "email": fake_factory.email(),
        "message": "random message",
        "author_note_visible": True,
    }

    service = InviteReviewer(
        workflow=assigned_article.articleworkflow,
        editor=section_editor.janeway_account,
        form_data=user_data,
        request=fake_request,
    )
    assert not JCOMProfile.objects.filter(email=user_data["email"]).exists()
    assert assigned_article.reviewassignment_set.count() == 0

    invited_user = service.run()
    assigned_article.refresh_from_db()
    invitation_token = generate_token(user_data["email"], assigned_article.journal.code)

    assert invited_user.janeway_account in assigned_article.journal.users_with_role("reviewer")
    assert not invited_user.is_active
    assert assigned_article.stage == "Under Review"
    assert assigned_article.reviewassignment_set.count() == 1
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assignment = assigned_article.reviewassignment_set.first()
    assert assignment.reviewer == invited_user.janeway_account
    assert assignment.editor == section_editor.janeway_account
    last_version = assigned_article.articleworkflow.get_review_versions(invited_user.janeway_account)[0]
    assert PermissionAssignment.objects.filter(
        user=invited_user.janeway_account,
        content_type_id=ContentType.objects.get_for_model(last_version.cover_letter.object).pk,
        object_id=last_version.cover_letter.object.pk,
        permission=PermissionAssignment.PermissionType.NO_NAMES,
        permission_secondary=PermissionAssignment.BinaryPermissionType.ALL,
    ).exists()

    review_assignment_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_assignment",
        journal=assigned_article.journal,
        request=fake_request,
        context={"article": assigned_article, "reviewer": invited_user.janeway_account},
        template_is_setting=True,
    )
    url = reverse(
        "wjs_evaluate_review",
        kwargs={"token": invitation_token, "assignment_id": assigned_article.reviewassignment_set.first().pk},
    )
    acceptance_url = f"{url}?access_code={assigned_article.reviewassignment_set.first().access_code}"

    # 1 notification to the reviewer (by InviteReviewer)
    # Check emails
    assert len(mail.outbox) == 1
    emails = [m for m in mail.outbox if m.to[0] == invited_user.email]
    assert len(emails) == 1
    assert review_assignment_subject in emails[0].subject
    assert assigned_article.journal.code in emails[0].subject
    assert "is a diamond open-access" in emails[0].body
    assert acceptance_url in emails[0].body.replace("\n", "")  # ATM, URL is broken by newline... why???
    assert "random message" in emails[0].body
    # Check messages
    assert Message.objects.count() == 1
    message_to_invited_user = Message.objects.first()
    assert message_to_invited_user.subject == review_assignment_subject
    assert "random message" in message_to_invited_user.body
    assert acceptance_url in message_to_invited_user.body
    assert "is a diamond open-access" in message_to_invited_user.body
    assert message_to_invited_user.message_type == Message.MessageTypes.SYSTEM
    assert message_to_invited_user.actor == section_editor.janeway_account
    assert list(message_to_invited_user.recipients.all()) == [invited_user.janeway_account]


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_handle_accept_invite_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_assignment_invited_user: review_models.ReviewAssignment,
    accept_gdpr: bool,
):
    """If the user accepts the invitation, assignment is accepted and user is confirmed if they accept GDPR."""

    invited_user = review_assignment_invited_user.reviewer
    assignment = assigned_article.reviewassignment_set.first()
    additional_comments = "Additional comments"
    evaluate_data = {
        "reviewer_decision": "1",
        "accept_gdpr": accept_gdpr,
        "additional_comments": additional_comments,
        "date_due": localtime(now()).date() + datetime.timedelta(days=21),
    }

    # Message related to the editor assignment
    assert Message.objects.count() == 1
    fake_request.user = invited_user
    evaluate = EvaluateReview(
        assignment=assignment,
        reviewer=invited_user,
        editor=section_editor.janeway_account,
        form_data=evaluate_data,
        request=fake_request,
        token=invited_user.jcomprofile.invitation_token,
    )
    if accept_gdpr:
        evaluate.run()
    else:
        with pytest.raises(ValidationError, match="Transition conditions not met"):
            evaluate.run()
    assignment.refresh_from_db()
    invited_user.refresh_from_db()
    invited_user.jcomprofile.refresh_from_db()

    if accept_gdpr:
        # Expected message:
        # - invitation to reviewer (note to reviewer)
        # - reviewer's acceptance - reviewer_acknowledgement (from rev to ed: rev accepts/declines assignment)
        # - acknowledgement to reviewer - review_accept_acknowledgement (from ed to rev: ed thanks rev for accepting)
        assert Message.objects.count() == 3
        # Message related to the reviewer accepting the assignment
        message = Message.objects.get(recipients__in=[section_editor.janeway_account])
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [section_editor.janeway_account]
        context = {
            "article": assignment.article,
            "request": fake_request,
            "review_assignment": assignment,
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": assignment.id}),
            "additional_comments": additional_comments,
        }

        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_reviewer_acknowledgement",
            journal=assignment.article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="reviewer_acknowledgement",
            journal=assignment.article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        assert message.subject == message_subject
        assert message.body == message_body
    else:
        # No new message created
        assert Message.objects.count() == 1
    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)

    assert not assignment.date_declined
    assert not assignment.is_complete
    calculated_date = localtime(now()).date() + datetime.timedelta(default_review_days)
    assert assignment.date_due == calculated_date

    if accept_gdpr:
        assert invited_user.is_active
        assert invited_user.jcomprofile.gdpr_checkbox
        assert not invited_user.jcomprofile.invitation_token
        assert assignment.date_accepted
    else:
        assert not invited_user.is_active
        assert not invited_user.jcomprofile.gdpr_checkbox
        assert invited_user.jcomprofile.invitation_token
        assert not assignment.date_accepted


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_handle_decline_invite_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_assignment_invited_user: review_models.ReviewAssignment,
    accept_gdpr: bool,
):
    """If the user declines the invitation, assignment is declined and user is confirmed if they accept GDPR."""

    invited_user = review_assignment_invited_user.reviewer
    assignment = assigned_article.reviewassignment_set.first()
    fake_request.GET = {"access_code": assignment.access_code}
    additional_comments = "Additional comments"
    evaluate_data = {
        "reviewer_decision": "0",
        "accept_gdpr": accept_gdpr,
        "additional_comments": additional_comments,
    }

    # Message related to the editor assignment
    assert Message.objects.count() == 1

    fake_request.user = invited_user
    evaluate = EvaluateReview(
        assignment=assignment,
        reviewer=invited_user,
        editor=section_editor.janeway_account,
        form_data=evaluate_data,
        request=fake_request,
        token=invited_user.jcomprofile.invitation_token,
    )
    evaluate.run()
    assignment.refresh_from_db()
    invited_user.refresh_from_db()
    # Regardless of the value of accept_gdpr, the message is created and sent
    # See also test_logic.test_handle_accept_invite_reviewer()
    assert Message.objects.count() == 3
    # Message related to the reviewer declining the assignment
    message = Message.objects.get(recipients__in=[section_editor.janeway_account])
    assert message.actor == invited_user
    assert list(message.recipients.all()) == [section_editor.janeway_account]
    context = {
        "article": assignment.article,
        "request": fake_request,
        "review_assignment": assignment,
        "review_url": reverse("wjs_review_review", kwargs={"assignment_id": assignment.id}),
        "additional_comments": additional_comments,
    }
    message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_reviewer_acknowledgement",
        journal=assignment.article.journal,
        request=fake_request,
        context=context,
        template_is_setting=True,
    )
    message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="reviewer_acknowledgement",
        journal=assignment.article.journal,
        request=fake_request,
        context=context,
        template_is_setting=True,
    )
    assert message.subject == message_subject
    assert message.body == message_body
    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    # Notification is from the system, not from the reviewer
    assert invited_user.signature not in message.body
    assert f"{assignment.article.journal.code} Journal" in message.body

    assert invited_user.is_active == accept_gdpr
    assert invited_user.jcomprofile.gdpr_checkbox == accept_gdpr
    assert bool(invited_user.jcomprofile.invitation_token) != accept_gdpr
    assert not assignment.date_accepted
    assert assignment.date_declined
    assert assignment.is_complete
    assert assignment.date_due == localtime(now()).date() + datetime.timedelta(default_review_days)


@pytest.mark.django_db
def test_handle_update_due_date_in_evaluate_review_one_day_in_the_future(
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: review_models.ReviewAssignment,
):
    """
    Test what happens if the user decides to postpone the due date, and it's just one day in the future with respect
    to the current due date.
    """

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    default_review_days_plus_one = default_review_days + 1
    default_date_due = localtime(now()).date() + datetime.timedelta(days=default_review_days)
    new_date_due = localtime(now()).date() + datetime.timedelta(days=default_review_days_plus_one)
    # Check that the new date due is not too far in the future (i.e. it does not trigger an EO message/notification)
    assert default_review_days_plus_one < settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD
    # Please note that Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a
    # datetime.datetime object
    assert review_assignment.date_due == default_date_due

    evaluate_data = {"reviewer_decision": "2", "date_due": new_date_due}

    # Message related to the editor assignment
    assert Message.objects.count() == 1

    fake_request.user = invited_user
    evaluate = EvaluateReview(
        assignment=review_assignment,
        reviewer=invited_user,
        editor=review_assignment.editor,
        form_data=evaluate_data,
        request=fake_request,
        token=invited_user.jcomprofile.invitation_token,
    )
    evaluate.run()
    review_assignment.refresh_from_db()

    # No new message created
    assert Message.objects.count() == 1

    # check that the due date is updated
    # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date object
    assert review_assignment.date_due == new_date_due


@pytest.mark.django_db
def test_handle_update_due_date_in_evaluate_review_far_in_the_future_triggers_a_message_to_eo(
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: review_models.ReviewAssignment,
):
    """
    Test what happens if the user decides to postpone the due date, and it's "far" in the future, so to trigger an EO
    message/notification.
    """

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    days_far_in_the_future = default_review_days + settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD + 1
    default_date_due = localtime(now()).date() + datetime.timedelta(days=default_review_days)
    new_date_due = localtime(now()).date() + datetime.timedelta(days=days_far_in_the_future)
    # Please note that Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a
    # datetime.datetime object
    assert review_assignment.date_due == default_date_due

    evaluate_data = {"reviewer_decision": "2", "date_due": new_date_due}

    eo_message_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="due_date_far_future_subject",
        request=fake_request,
        context={"reviewer": review_assignment.reviewer},
        journal=fake_request.journal,
    )

    # Message related to the editor assignment
    assert Message.objects.count() == 1
    assert Message.objects.first().subject != eo_message_subject

    fake_request.user = invited_user
    evaluate = EvaluateReview(
        assignment=review_assignment,
        reviewer=invited_user,
        editor=review_assignment.editor,
        form_data=evaluate_data,
        request=fake_request,
        token=invited_user.jcomprofile.invitation_token,
    )
    evaluate.run()
    review_assignment.refresh_from_db()

    # One new message created
    assert Message.objects.count() == 2
    eo_message = Message.objects.get(subject=eo_message_subject)
    assert list(eo_message.recipients.all()) == [get_eo_user(review_assignment.article)]

    # check that the due date is updated
    # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date object
    assert review_assignment.date_due == new_date_due


@pytest.mark.django_db
def test_handle_update_due_date_in_evaluate_review_in_the_past(
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: review_models.ReviewAssignment,
):
    """If the user decides to postpone the due date, and it's in the past with respect to the current due date."""

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    assert review_assignment.date_due == localtime(now()).date() + datetime.timedelta(default_review_days)
    new_date_due = review_assignment.date_due - datetime.timedelta(days=1)

    evaluate_data = {"reviewer_decision": "2", "date_due": new_date_due}

    # Message related to the editor assignment
    assert Message.objects.count() == 1

    fake_request.user = invited_user
    evaluate = EvaluateReview(
        assignment=review_assignment,
        reviewer=invited_user,
        editor=review_assignment.editor,
        form_data=evaluate_data,
        request=fake_request,
        token=invited_user.jcomprofile.invitation_token,
    )
    evaluate.run()
    review_assignment.refresh_from_db()

    # No new message created
    assert Message.objects.count() == 1

    # Check that the low level logic class allows to update the due date even if it's in the past
    # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date object
    assert review_assignment.date_due == new_date_due


# TODO: test failure in AssignToReviewer are bubbled up


@pytest.mark.django_db
def test_invite_reviewer_but_user_already_exists(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """A user can be invited but if the email is of an existing user the assignment is automatically created."""
    fake_request.user = section_editor.janeway_account

    user_data = {
        "first_name": normal_user.first_name,
        "last_name": normal_user.last_name,
        "email": normal_user.email,
        "message": "random message",
    }

    service = InviteReviewer(
        workflow=assigned_article.articleworkflow,
        editor=section_editor.janeway_account,
        form_data=user_data,
        request=fake_request,
    )
    assert JCOMProfile.objects.filter(email=user_data["email"]).exists()
    assert assigned_article.reviewassignment_set.count() == 0

    invited_user = service.run()
    assigned_article.refresh_from_db()

    assert invited_user == normal_user
    assert invited_user.janeway_account in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Under Review"
    assert assigned_article.reviewassignment_set.count() == 1
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assignment = assigned_article.reviewassignment_set.first()
    assert assignment.reviewer == invited_user.janeway_account
    assert assignment.editor == section_editor.janeway_account
    assert len(mail.outbox) == 1
    # Check messages
    assert Message.objects.count() == 1
    subject_from_setting = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_assignment",
        journal=assigned_article.journal,
        request=fake_request,
        context={"reviewer": normal_user.janeway_account},
        template_is_setting=True,
    )
    message_to_reviewer = Message.objects.get(subject=subject_from_setting)
    assert "random message" in message_to_reviewer.body
    assert message_to_reviewer.message_type == Message.MessageTypes.SYSTEM
    assert message_to_reviewer.actor == section_editor.janeway_account
    assert list(message_to_reviewer.recipients.all()) == [normal_user.janeway_account]


@patch("plugins.wjs_review.logic.events_logic.Events.raise_event")
@pytest.mark.parametrize(
    "submit_final",
    (True, False),
)
@pytest.mark.django_db
def test_submit_review(
    raise_event,
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    review_form: review_models.ReviewForm,
    submit_final: bool,
):
    """
    If the reviewer submits a review, reviewassignment is marked as complete (and accepted if not).
    """

    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(is_complete=False).count() == 1
    fake_request.user = review_assignment.reviewer
    _submit_review(review_assignment, fake_request, submit_final)
    assert assigned_article.reviewassignment_set.all().count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=True).count() == 1

    if submit_final:
        # When submitting a review, reviewassignment is marked as accepted
        assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=False).count() == 1
        assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 1
        raise_event.assert_called_with(
            "on_review_complete",
            task_object=assigned_article,
            review_assignment=review_assignment,
            request=fake_request,
        )
    else:
        # review_assignment is not accepted by the user
        assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
        assert assigned_article.reviewassignment_set.filter(is_complete=False).count() == 1
        raise_event.assert_not_called()


@patch("plugins.wjs_review.logic.events_logic.Events.raise_event")
@pytest.mark.parametrize(
    "submit_final",
    (True, False),
)
@pytest.mark.django_db
def test_submit_review_messages(
    raise_event,
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    review_form: review_models.ReviewForm,
    submit_final: bool,
):
    """
    If the reviewer submits a review, two messages are created, and sent to the editor and
    the reviewer.
    """

    fake_request.user = review_assignment.reviewer
    assert Message.objects.count() == 1
    _submit_review(review_assignment, fake_request, submit_final)
    assert Message.objects.count() == 3
    message_to_the_reviewer = (
        Message.objects.filter(recipients__pk=review_assignment.reviewer.pk).order_by("created").last()
    )
    reviewer_message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_complete_reviewer_acknowledgement",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "review_assignment": review_assignment,
            "article": assigned_article,
        },
        template_is_setting=True,
    )
    reviewer_message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="review_complete_reviewer_acknowledgement",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "review_assignment": review_assignment,
            "article": assigned_article,
        },
        template_is_setting=True,
    )
    assert message_to_the_reviewer.subject == reviewer_message_subject
    assert message_to_the_reviewer.body == reviewer_message_body
    assert message_to_the_reviewer.message_type == Message.MessageTypes.SYSTEM
    message_to_the_editor = Message.objects.get(recipients__pk=review_assignment.editor.pk)
    context = {
        "review_assignment": review_assignment,
        "article": assigned_article,
    }
    editor_message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_complete_acknowledgement",
        journal=assigned_article.journal,
        request=fake_request,
        context=context,
        template_is_setting=True,
    )
    assert message_to_the_editor.subject == editor_message_subject
    editor_message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="review_complete_acknowledgement",
        journal=assigned_article.journal,
        request=fake_request,
        context=context,
        template_is_setting=True,
    )
    assert message_to_the_editor.body == editor_message_body.replace("<br/>", "<br>")  # FIXME: janeway settings?
    assert message_to_the_editor.message_type == Message.MessageTypes.SYSTEM


@pytest.mark.parametrize(
    "initial_state,decision,final_state",
    (
        (
            ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            "dispatch",
            ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED,
        ),
    ),
)
@pytest.mark.django_db
def test_handle_issues_to_selected(
    review_settings,
    fake_request: HttpRequest,
    article: submission_models.Article,
    eo_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    initial_state: str,
    decision: str,
    final_state: str,
):
    """
    If the EO deems a paper's issues not important, article.stage and workflow.state are set as expected.
    """
    article.stage = STAGE
    article.save()
    article.articleworkflow.state = initial_state
    article.articleworkflow.save()
    fake_request.user = eo_user
    # Reset email and messages just before running the service
    mail.outbox = []
    Message.objects.all().delete()

    handle = AdminActions(
        workflow=article.articleworkflow,
        user=eo_user,
        request=fake_request,
        decision="dispatch",
    )
    handle.run()
    article.refresh_from_db()
    article.articleworkflow.refresh_from_db()
    if decision == "dispatch":
        assert article.stage == STAGE
        assert article.articleworkflow.state == final_state
        assert Message.objects.count() == 1
        message = Message.objects.get()
        context = {
            "article": article,
            "request": fake_request,
        }
        requeue_article_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="requeue_article_subject",
            journal=article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        requeue_article_message = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="requeue_article_body",
            journal=article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        assert message.subject == requeue_article_subject
        assert message.body == requeue_article_message
        assert message.message_type == message.MessageTypes.SYSTEM
        # no notification sent to EO
        assert message.recipients.count() == 0
        # no email because message verbosity is TIMELINE
        assert message.verbosity == Message.MessageVerbosity.TIMELINE
        assert len(mail.outbox) == 0


@pytest.mark.parametrize(
    "initial_state,decision,final_state",
    (
        (
            ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            "dispatch",
            ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED,
        ),
    ),
)
@pytest.mark.django_db
def test_handle_issues_to_selected_wrong_user(
    fake_request: HttpRequest,
    article: submission_models.Article,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    initial_state: str,
    decision: str,
    final_state: str,
):
    """
    If user validation fails in business logic, article stages are not changed.
    """
    article.articleworkflow.state = initial_state
    article.articleworkflow.save()
    fake_request.user = jcom_user
    # Reset email and messages just before calling HandleDecision.run()
    mail.outbox = []
    Message.objects.all().delete()

    with pytest.raises(ValidationError):
        handle = AdminActions(
            workflow=article.articleworkflow,
            user=jcom_user,
            request=fake_request,
            decision="dispatch",
        )
        handle.run()
    article.refresh_from_db()
    article.articleworkflow.refresh_from_db()
    if decision == "dispatch":
        assert article.stage == submission_models.STAGE_UNSUBMITTED
        assert article.articleworkflow.state == initial_state
        assert Message.objects.count() == 0
        assert len(mail.outbox) == 0


@pytest.mark.parametrize(
    "initial_state,decision",
    (
        (
            ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
            "dispatch",
        ),
        (
            ArticleWorkflow.ReviewStates.SUBMITTED,
            "dispatch",
        ),
    ),
)
@pytest.mark.django_db
def test_handle_issues_to_selected_wrong_state(
    fake_request: HttpRequest,
    article: submission_models.Article,
    eo_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    initial_state: str,
    decision: str,
):
    """
    If initial state validation fails in business logic, article stages are not changed.
    """
    article.articleworkflow.state = initial_state
    article.articleworkflow.save()
    fake_request.user = eo_user
    # Reset email and messages just before calling HandleDecision.run()
    mail.outbox = []
    Message.objects.all().delete()

    with pytest.raises(ValidationError):
        handle = AdminActions(
            workflow=article.articleworkflow,
            user=eo_user,
            request=fake_request,
            decision="dispatch",
        )
        handle.run()
    article.refresh_from_db()
    article.articleworkflow.refresh_from_db()
    if decision == "dispatch":
        assert article.stage == submission_models.STAGE_UNSUBMITTED
        assert article.articleworkflow.state == initial_state
        assert Message.objects.count() == 0
        assert len(mail.outbox) == 0


@pytest.mark.parametrize(
    "initial_state,decision,final_state",
    (
        (
            ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
            ArticleWorkflow.ReviewStates.NOT_SUITABLE,
        ),
        (
            ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION,
            ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
        ),
    ),
)
@pytest.mark.django_db
def test_handle_admin_decision(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    eo_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    initial_state: str,
    decision: str,
    final_state: str,
):
    """
    When EO makes an admin-only decision, the article stage is updated and messages are created and sent.
    """
    assigned_article.articleworkflow.state = initial_state
    assigned_article.articleworkflow.save()
    fake_request.user = eo_user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
    }
    # Reset email and messages just before calling HandleDecision.run()
    mail.outbox = []
    Message.objects.all().delete()
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=eo_user,
        request=fake_request,
        admin_form=True,
    )
    handle.run()
    assigned_article.refresh_from_db()
    if decision == ArticleWorkflow.Decisions.NOT_SUITABLE:
        assert assigned_article.stage == submission_models.STAGE_REJECTED
        assert assigned_article.articleworkflow.state == final_state
        review = assigned_article.reviewassignment_set.first()
        # Prepare subject and body
        message_context = {
            "article": assigned_article,
            "request": fake_request,
            "revision": None,
            "decision": form_data["decision"],
            "user_message_content": form_data["decision_editor_report"],
            "skip": False,
            "recipient": review.reviewer,
        }
        assert Message.objects.count() == 2
        withdrawn_message = Message.objects.first()
        not_suitable_message = Message.objects.last()
        not_suitable_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_not_suitable_subject",
            journal=assigned_article.journal,
        ).processed_value
        not_suitable_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_not_suitable_body",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_context,
            template_is_setting=True,
        )
        not_suitable_message_body = raw(not_suitable_message_body)

        # See here for a map of which setting is used in each withdrawal situation:
        # https://gitlab.sissamedialab.it/wjs/specs/-/issues/1089#review-assignments-withdrawal
        withdrawn_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="editor_deassign_reviewer_subject",
            journal=assigned_article.journal,
        ).processed_value
        withdrawn_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="editor_deassign_reviewer_default",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_context,
            template_is_setting=True,
        )
        # Try to avoid test-rot and simplify comparison:
        withdrawn_message_body = raw(withdrawn_message_body)

        # Check the message
        assert not_suitable_message.actor == eo_user.janeway_account
        assert list(not_suitable_message.recipients.all()) == [assigned_article.correspondence_author]
        assert not_suitable_message.subject == not_suitable_message_subject
        assert not_suitable_message_body in raw(not_suitable_message.body)
        assert withdrawn_message.actor == eo_user.janeway_account
        assert list(withdrawn_message.recipients.all()) == [review.reviewer]
        assert withdrawn_message.subject == withdrawn_message_subject
        assert withdrawn_message_body in raw(withdrawn_message.body)
        # Check the mail
        assert len(mail.outbox) == 2
        # In HandleDecision.run,
        # - first we _withdraw_unfinished_review_requests
        # - then we _log_not_suitable
        # so the _last_ email is the one about the "not suitable" notification to the author
        not_suitable_mail = mail.outbox[1]
        assert not_suitable_message_subject in not_suitable_mail.subject
        assert not_suitable_message_body in raw(not_suitable_message.body)
        withdrawn_mail = mail.outbox[0]
        assert withdrawn_message_subject in withdrawn_mail.subject
        assert withdrawn_message_body in raw(withdrawn_mail.body)


@pytest.mark.parametrize(
    "initial_state,decision,final_state",
    (
        (
            ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
            ArticleWorkflow.ReviewStates.NOT_SUITABLE,
        ),
        (
            ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
            ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION,
            ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
        ),
    ),
)
@pytest.mark.django_db
def test_handle_admin_decision_wrong_user(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    initial_state: str,
    decision: str,
    final_state: str,
):
    """
    If user validation fails in business logic, article stages are not changed.
    """
    assigned_article.articleworkflow.state = initial_state
    assigned_article.articleworkflow.save()
    fake_request.user = jcom_user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
    }
    # Reset email and messages just before calling HandleDecision.run()
    mail.outbox = []
    Message.objects.all().delete()
    with pytest.raises(ValidationError):
        handle = HandleDecision(
            workflow=assigned_article.articleworkflow,
            form_data=form_data,
            user=jcom_user,
            request=fake_request,
            admin_form=True,
        )
        handle.run()
    assigned_article.refresh_from_db()
    if decision == ArticleWorkflow.Decisions.NOT_SUITABLE:
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
        assert assigned_article.articleworkflow.state == initial_state
    elif decision == ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION:
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
        assert assigned_article.articleworkflow.state == initial_state


@pytest.mark.parametrize(
    "initial_state,decision,final_state",
    (
        (
            ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
            ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        ),
        (
            ArticleWorkflow.ReviewStates.SUBMITTED,
            ArticleWorkflow.Decisions.NOT_SUITABLE,
            ArticleWorkflow.ReviewStates.SUBMITTED,
        ),
        (
            ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
            ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION,
            ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
        ),
        (
            ArticleWorkflow.ReviewStates.SUBMITTED,
            ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION,
            ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
        ),
    ),
)
@pytest.mark.django_db
def test_handle_admin_decision_wrong_state(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    eo_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    initial_state: str,
    decision: str,
    final_state: str,
):
    """
    If initial state validation fails in business logic, article stages are not changed.
    """
    assigned_article.articleworkflow.state = initial_state
    assigned_article.articleworkflow.save()
    fake_request.user = eo_user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
    }
    # Reset email and messages just before calling HandleDecision.run()
    mail.outbox = []
    Message.objects.all().delete()
    with pytest.raises(ValidationError):
        handle = HandleDecision(
            workflow=assigned_article.articleworkflow,
            form_data=form_data,
            user=eo_user,
            request=fake_request,
            admin_form=True,
        )
        handle.run()
    assigned_article.refresh_from_db()
    if decision == ArticleWorkflow.Decisions.NOT_SUITABLE:
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
        assert assigned_article.articleworkflow.state == initial_state
    elif decision == ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION:
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
        assert assigned_article.articleworkflow.state == initial_state


@pytest.mark.parametrize(
    "decision,final_state",
    (
        (ArticleWorkflow.Decisions.ACCEPT, ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER),
        (ArticleWorkflow.Decisions.REJECT, ArticleWorkflow.ReviewStates.REJECTED),
        (ArticleWorkflow.Decisions.NOT_SUITABLE, ArticleWorkflow.ReviewStates.NOT_SUITABLE),
        (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, ArticleWorkflow.ReviewStates.TO_BE_REVISED),
    ),
)
@pytest.mark.django_db
def test_handle_editor_decision(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    decision: str,
    final_state: str,
):
    """
    If the editor makes a decision, article.stage is set to the next workflow stage if decision is final
    and articleworkflow.state is updated according to the decision.
    """
    editor_user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = jcom_user
    review_2 = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=jcom_user,
        assigned_article=assigned_article,
    )
    _submit_review(review_2, fake_request)
    # Ensure initial data is consistent: review_2 is accepted and complete, review_assignment is not
    assert assigned_article.reviewassignment_set.all().count() == 2
    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 0
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 1

    fake_request.user = editor_user
    form_data = {
        "decision": decision,
        "withdraw_notice": "notice",
        **(
            {"decision_editor_report": "random message"}
            if decision != ArticleWorkflow.Decisions.TECHNICAL_REVISION
            else {}
        ),
    }
    if final_state not in (
        ArticleWorkflow.ReviewStates.ACCEPTED,
        ArticleWorkflow.ReviewStates.REJECTED,
        ArticleWorkflow.ReviewStates.NOT_SUITABLE,
    ):
        form_data["date_due"] = now().date() + datetime.timedelta(days=7)
    # Reset email and messages just before calling HandleDecision.run()
    mail.outbox = []
    Message.objects.all().delete()
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=editor_user,
        request=fake_request,
    )
    editor_decision = handle.run()
    try:
        revision = EditorRevisionRequest.objects.get(
            article=assigned_article, review_round=assigned_article.current_review_round_object()
        )
        assert revision.editor_decision == editor_decision
    except EditorRevisionRequest.DoesNotExist:
        pass
    assigned_article.refresh_from_db()

    # We had an incomplete review assignment; when the editor makes a decision (except for technical revision),
    # these are withdrawn and the reviewer informed.
    # Test this case for all the states at once to ease detailed testing.
    message_template_context = {
        "article": assigned_article,
        "request": fake_request,
        "decision": form_data["decision"],
        "user_message_content": (
            form_data["decision_editor_report"]
            if form_data["decision"] != ArticleWorkflow.Decisions.TECHNICAL_REVISION
            else ""
        ),
        "withdraw_notice": form_data["withdraw_notice"],
        "skip": False,
        "recipient": review_assignment.reviewer,
    }
    review_withdraw_message_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="editor_deassign_reviewer_subject",
        journal=assigned_article.journal,
        request=fake_request,
        context=message_template_context,
        template_is_setting=True,
    )

    # In all the cases except technical revision, pending review assignments are
    # withdrawn. In this case the reviewer receive a message to notify this
    # At this stage two messages exist:
    # - first message: RA withdraw notice to the reviewer, created in any case
    #   (which is always created before any further action thus it's guaranteed to be the first message in the queue)
    # - second message: decision specific message
    # In order to avoid duplicated code, we test the withdraw messages here and then remove withdraw notifications
    # to test condition-specific messages futher down the test
    # TECHNICAL_REVISION does not cause a withdrawal of the review assignment, so it's skipped here
    if decision not in (ArticleWorkflow.Decisions.TECHNICAL_REVISION,):
        # Message body is taken from the form, not from the setting as we use the setting only to initialize the form
        review_withdraw_message_body = form_data["withdraw_notice"]
        assert Message.objects.count() == 2
        withdrawn_review_message = Message.objects.order_by("created").first()
        assert withdrawn_review_message.subject == review_withdraw_message_subject
        assert review_withdraw_message_body == withdrawn_review_message.body
        assert withdrawn_review_message.message_type == Message.MessageTypes.SYSTEM
        assert len(mail.outbox) == 2
        withdrawn_review_mail = mail.outbox[0]
        assert review_withdraw_message_subject in withdrawn_review_mail.subject
        # If message must be split, check that the first part is in the email body
        if Message.SPLIT_MARKER in review_withdraw_message_body:
            assert review_withdraw_message_body.partition(Message.SPLIT_MARKER)[0] in raw(withdrawn_review_mail.body)
        else:
            assert review_withdraw_message_body in raw(withdrawn_review_mail.body)

        Message.objects.get(subject=review_withdraw_message_subject).delete()
        for message in mail.outbox:
            if review_withdraw_message_subject in message.subject:
                mail.outbox.remove(message)
                break

    if decision in (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.Decisions.MINOR_REVISION):
        # article is kept the as ON_WORKFLOW_ELEMENT_COMPLETE event is not triggered
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVISION
        assert assigned_article.articleworkflow.state == final_state
        revision = EditorRevisionRequest.objects.get(
            article=assigned_article,
            review_round=review_assignment.review_round,
        )
        assert revision.editor_note == "random message"
        assert revision.date_due == form_data["date_due"]
        assert revision.type == form_data["decision"]
        # Prepare subjects and bodies
        message_template_context = {
            "article": assigned_article,
            "request": fake_request,
            "revision": revision,
            "major_revision": revision.type == ArticleWorkflow.Decisions.MAJOR_REVISION,
            "minor_revision": revision.type == ArticleWorkflow.Decisions.MINOR_REVISION,
            "tech_revision": revision.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            "decision": form_data["decision"],
            "user_message_content": (
                form_data["decision_editor_report"]
                if form_data["decision"] != ArticleWorkflow.Decisions.TECHNICAL_REVISION
                else ""
            ),
            "withdraw_notice": form_data["withdraw_notice"],
            "skip": False,
        }
        revision_request_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_request_revisions",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_template_context,
            template_is_setting=True,
        )
        revision_request_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="request_revisions",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_template_context,
            template_is_setting=True,
        )
        revision_request_message_body = raw(revision_request_message_body)

        # Check the messages - withdraw message testsed above
        assert Message.objects.count() == 1
        revision_request_message = Message.objects.order_by("created").last()
        assert revision_request_message.subject == revision_request_message_subject
        assert revision_request_message_body in raw(revision_request_message.body)
        assert revision_request_message.message_type == Message.MessageTypes.SYSTEM
        # Check the emails - withdraw message testsed above
        assert len(mail.outbox) == 1
        revision_request_mail = mail.outbox[0]
        assert revision_request_message_subject in revision_request_mail.subject
        # If message must be split, check that the first part is in the email body
        if Message.SPLIT_MARKER in revision_request_message_body:
            assert revision_request_message_body.partition(Message.SPLIT_MARKER)[0] in raw(revision_request_mail.body)
        else:
            assert revision_request_message_body in raw(revision_request_mail.body)
    elif decision == ArticleWorkflow.Decisions.ACCEPT:
        assert assigned_article.stage == submission_models.STAGE_ACCEPTED
        assert assigned_article.articleworkflow.state == final_state
        # Prepare subject and body
        accept_message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_decision_accept",
            journal=assigned_article.journal,
        ).processed_value
        accept_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_decision_accept",
            journal=assigned_article.journal,
            request=fake_request,
            context={
                "article": assigned_article,
                "request": fake_request,
                "revision": None,
                "decision": form_data["decision"],
                "user_message_content": (
                    form_data["decision_editor_report"]
                    if form_data["decision"] != ArticleWorkflow.Decisions.TECHNICAL_REVISION
                    else ""
                ),
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        accept_message_body = raw(accept_message_body)

        # Check the message
        assert Message.objects.count() == 1
        accept_message = Message.objects.get()
        assert accept_message.actor == editor_user
        assert list(accept_message.recipients.all()) == [assigned_article.correspondence_author]
        assert accept_message_subject == accept_message.subject
        assert accept_message_body in raw(accept_message.body)
        assert accept_message.message_type == Message.MessageTypes.SYSTEM
        # Check that one email is sent by us (and not by Janeway)
        assert len(mail.outbox) == 1
        accept_mail = mail.outbox[0]
        assert accept_message_subject in accept_mail.subject
        # If message must be split, check that the first part is in the email body
        if Message.SPLIT_MARKER in accept_message_body:
            assert accept_message_body.partition(Message.SPLIT_MARKER)[0] in raw(accept_mail.body)
        else:
            assert accept_message_body in raw(accept_mail.body)
    elif decision == ArticleWorkflow.Decisions.NOT_SUITABLE:
        assert assigned_article.stage == submission_models.STAGE_REJECTED
        assert assigned_article.articleworkflow.state == final_state
        # Prepare subject and body
        not_suitable_message = Message.objects.get()
        not_suitable_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_not_suitable_subject",
            journal=assigned_article.journal,
        ).processed_value
        not_suitable_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_not_suitable_body",
            journal=assigned_article.journal,
            request=fake_request,
            context={
                "article": assigned_article,
                "request": fake_request,
                "revision": None,
                "decision": form_data["decision"],
                "user_message_content": (
                    form_data["decision_editor_report"]
                    if form_data["decision"] != ArticleWorkflow.Decisions.TECHNICAL_REVISION
                    else ""
                ),
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        not_suitable_message_body = raw(not_suitable_message_body)

        # Check the message
        assert Message.objects.count() == 1
        assert not_suitable_message.actor == editor_user
        assert list(not_suitable_message.recipients.all()) == [assigned_article.correspondence_author]
        assert not_suitable_message.subject == not_suitable_message_subject
        assert not_suitable_message_body in raw(not_suitable_message.body)
        # Check the mail
        assert len(mail.outbox) == 1
        not_suitable_mail = mail.outbox[0]
        assert not_suitable_message_subject in not_suitable_mail.subject
        # If message must be split, check that the first part is in the email body
        if Message.SPLIT_MARKER in not_suitable_message_body:
            assert not_suitable_message_body.partition(Message.SPLIT_MARKER)[0] in raw(not_suitable_mail.body)
        else:
            assert not_suitable_message_body in raw(not_suitable_mail.body)
    elif decision == ArticleWorkflow.Decisions.REJECT:
        assert assigned_article.stage == submission_models.STAGE_REJECTED
        assert assigned_article.articleworkflow.state == final_state
        # Prepare subject and body
        reject_message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_decision_decline",
            journal=assigned_article.journal,
        ).processed_value
        reject_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_decision_decline",
            journal=assigned_article.journal,
            request=fake_request,
            context={
                "article": assigned_article,
                "request": fake_request,
                "revision": None,
                "decision": form_data["decision"],
                "user_message_content": (
                    form_data["decision_editor_report"]
                    if form_data["decision"] != ArticleWorkflow.Decisions.TECHNICAL_REVISION
                    else ""
                ),
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        reject_message_body = raw(reject_message_body)

        # Check the message
        assert Message.objects.count() == 1
        reject_message = Message.objects.get()
        assert reject_message.actor == editor_user
        assert list(reject_message.recipients.all()) == [assigned_article.correspondence_author]
        assert reject_message.subject == reject_message_subject
        assert reject_message_body in raw(reject_message.body)
        assert reject_message.message_type == Message.MessageTypes.SYSTEM
        # Check that one email is sent by us (and not by Janeway)
        assert len(mail.outbox) == 1
        reject_mail = mail.outbox[0]
        assert reject_message_subject in reject_mail.subject
        # If message must be split, check that the first part is in the email body
        if Message.SPLIT_MARKER in reject_message_body:
            assert reject_message_body.partition(Message.SPLIT_MARKER)[0] in raw(reject_mail.body)
        else:
            assert reject_message_body in raw(reject_mail.body)
    elif decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
        assert assigned_article.articleworkflow.state == final_state
        # Prepare subject and body
        technical_revision_message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="technical_revision_subject",
            journal=assigned_article.journal,
            request=fake_request,
            context={"article": assigned_article},
            template_is_setting=True,
        )
        technical_revision_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="technical_revision_body",
            journal=assigned_article.journal,
            request=fake_request,
            context={
                "article": assigned_article,
                "request": fake_request,
                "revision": revision,
                "decision": form_data["decision"],
                "user_message_content": None,
                "skip": False,
            },
            template_is_setting=True,
        )
        technical_revision_message_body = raw(technical_revision_message_body)

        # Check the message
        assert Message.objects.count() == 1
        technical_revision_message = Message.objects.get()
        assert technical_revision_message.actor == editor_user
        assert list(technical_revision_message.recipients.all()) == [assigned_article.correspondence_author]
        assert technical_revision_message.subject == technical_revision_message_subject
        assert technical_revision_message_body in raw(technical_revision_message.body)
        assert technical_revision_message.message_type == Message.MessageTypes.SYSTEM
        # Check that one email is sent by us (and not by Janeway)
        assert len(mail.outbox) == 1
        reject_mail = mail.outbox[0]
        assert reject_mail.recipients() == [assigned_article.correspondence_author.email]
        assert technical_revision_message_subject in reject_mail.subject
        # If message must be split, check that the first part is in the email body
        if Message.SPLIT_MARKER in technical_revision_message_body:
            assert technical_revision_message_body.partition(Message.SPLIT_MARKER)[0] in raw(reject_mail.body)
        else:
            assert technical_revision_message_body in raw(reject_mail.body)

    # Assert conditions on model instances
    if decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        # All review assignments are marked as complete; the one that was pending when the editor decision was take is
        # marked as "withdrawn".
        assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
        assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 0
        assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 1
        assert assigned_article.reviewassignment_set.filter(decision="withdrawn").count() == 0
    else:
        # All review assignments are marked as complete; the one that was pending when the editor decision was take is
        # marked as "withdrawn".
        assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
        assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 0
        assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 2
        assert assigned_article.reviewassignment_set.filter(decision="withdrawn").count() == 1

    editor_decision = EditorDecision.objects.get(
        workflow=assigned_article.articleworkflow,
        review_round=assigned_article.articleworkflow.article.current_review_round_object(),
    )
    assert editor_decision.decision == decision
    if decision != ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assert editor_decision.decision_editor_report == form_data["decision_editor_report"]
    else:
        assert editor_decision.decision_editor_report == ""


@pytest.mark.parametrize(
    "decision,confirm_version",
    (
        (ArticleWorkflow.Decisions.MINOR_REVISION, True),
        (ArticleWorkflow.Decisions.MINOR_REVISION, False),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, True),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, False),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, True),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, False),
        ("something", False),
    ),
)
@pytest.mark.django_db
def test_author_handle_revision(
    assigned_article: submission_models.Article,
    fake_request: HttpRequest,
    review_assignment: review_models.ReviewAssignment,
    decision: str,
    confirm_version: bool,
):
    """
    Author submitting a revision change the article state.

    If it's a technical revision, the author submits the updated article title and abstract as a first step and then
    submits the revision (which is handled by the AuthorHandleRevision service): we are actually testing the latter.

    In this case the revision round is not updated.

    If it's a minor or major revision, the author uploads updated files using an existing Janeway's view which does not
    trigger our logic and then submits the revision (which is handled by the AuthorHandleRevision service).
    """
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    author = assigned_article.correspondence_author
    fake_request.user = editor
    original_review_round = assigned_article.current_review_round()
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=WjsEditorAssignment.objects.get_current(assigned_article).editor,
        request=fake_request,
    )

    if decision not in HandleDecision._decision_handlers:
        with pytest.raises(ValidationError):
            handle.run()
    else:
        handle.run()
        assigned_article.refresh_from_db()
        revision = EditorRevisionRequest.objects.get(article=assigned_article)
        view_obj = ArticleRevisionUpdate()
        view_obj.object = revision
        view_obj.confirm_version = confirm_version
        form_class = view_obj.get_form_class()

        if decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            edit_form_class = view_obj._get_metadata_form_class()
            edit_form_data = {
                "title": "title",
                "abstract": "abstract",
            }
            edit_form = edit_form_class(data=edit_form_data, instance=revision)
            assert edit_form.is_valid()
            edit_form.save()
            if confirm_version:
                confirm_form_data = {
                    "author_note": "author_note_confirm",
                    "confirm_version": "on",
                }
            else:
                confirm_form_data = {
                    "author_note": "author_note_edit",
                    "confirm_cover_metadata": "on",
                }
        else:
            if confirm_version:
                confirm_form_data = {
                    "author_note": "author_note_confirm",
                    "confirm_version": "on",
                }
            else:
                confirm_form_data = {
                    "author_note": "author_note_edit",
                    "confirm_title": "on",
                    "confirm_styles": "on",
                    "confirm_blind": "on",
                    "confirm_cover": "on",
                }
        form = form_class(data=confirm_form_data, instance=revision, request=fake_request, user=author)
        assert form.is_valid()
        form.save()

        form.finish()
        assigned_article.refresh_from_db()
        revision.refresh_from_db()
        # we need a stable ordering of the messages because we pick them in specific order
        messages = Message.objects.all().order_by("created")
        assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
        if decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
            technical_revision_message_subject = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="technical_revision_subject",
                journal=assigned_article.journal,
                request=fake_request,
                context={"article": assigned_article},
                template_is_setting=True,
            )
            technicalrevisions_complete_reviewer_notification_subject = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="technicalrevisions_complete_reviewer_notification_subject",
                journal=assigned_article.journal,
                request=fake_request,
                context={"article": assigned_article},
                template_is_setting=True,
            )
            assert assigned_article.title == "title"
            assert assigned_article.abstract == "abstract"
            assert assigned_article.current_review_round() == original_review_round
            assert messages.count() == 4
            # messages.all()[0] contains reviewer invitation, ignore it
            # Author notification
            metadata_change_open_notification = messages.all()[1]
            assert metadata_change_open_notification.actor == editor
            assert list(metadata_change_open_notification.recipients.all()) == [assigned_article.correspondence_author]
            assert metadata_change_open_notification.subject == technical_revision_message_subject
            # Editor notification for updated metadata
            technical_revision_submission_editor_message = messages.all()[2]
            assert technical_revision_submission_editor_message.actor == get_system_user()
            assert list(technical_revision_submission_editor_message.recipients.all()) == [editor]
            assert (
                technical_revision_submission_editor_message.subject
                == technicalrevisions_complete_reviewer_notification_subject
            )
            # Reviewer notification for updated metadata
            technical_revision_submission_reviewer_message = messages.all()[3]
            assert technical_revision_submission_reviewer_message.actor == editor
            assert list(technical_revision_submission_reviewer_message.recipients.all()) == [
                review_assignment.reviewer
            ]
            assert (
                technical_revision_submission_reviewer_message.subject
                == technicalrevisions_complete_reviewer_notification_subject
            )
        else:
            assert assigned_article.current_review_round() == original_review_round + 1
            subject_request_revisions = render_template_from_setting(
                setting_group_name="email_subject",
                setting_name="subject_request_revisions",
                journal=assigned_article.journal,
                request=fake_request,
                context={
                    "article": assigned_article,
                    "major_revision": revision.type == ArticleWorkflow.Decisions.MAJOR_REVISION,
                    "minor_revision": revision.type == ArticleWorkflow.Decisions.MINOR_REVISION,
                },
                template_is_setting=True,
            )
            review_withdraw_notification_subject = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="editor_deassign_reviewer_subject",
                journal=assigned_article.journal,
                request=fake_request,
                context={"article": assigned_article},
                template_is_setting=True,
            )
            subject_revisions_complete_receipt_subject = render_template_from_setting(
                setting_group_name="email_subject",
                setting_name="subject_revisions_complete_receipt",
                journal=assigned_article.journal,
                request=fake_request,
                context={"article": assigned_article},
                template_is_setting=True,
            )
            assert assigned_article.current_review_round() == original_review_round + 1
            assert messages.count() == 5
            # messages.all()[0] contains reviewer invitation, ignore it
            # Withdraw notification to reviewer
            review_withdraw_notification = messages.all()[1]
            assert review_withdraw_notification.actor == editor
            assert review_withdraw_notification.subject == review_withdraw_notification_subject
            assert list(review_withdraw_notification.recipients.all()) == [review_assignment.reviewer]
            # Author notification for revision request
            request_revisions_author_message = messages.all()[2]
            assert request_revisions_author_message.actor == editor
            assert list(request_revisions_author_message.recipients.all()) == [assigned_article.correspondence_author]
            assert request_revisions_author_message.subject == subject_request_revisions
            # Editor notification for revision submission
            revision_complete_editor_message = messages.all()[3]
            assert revision_complete_editor_message.actor == get_system_user()
            assert list(revision_complete_editor_message.recipients.all()) == [review_assignment.editor]
            assert revision_complete_editor_message.subject == subject_revisions_complete_receipt_subject
            # Author notification of successful revision submission
            revision_complete_author_message = messages.all()[4]
            assert revision_complete_author_message.actor == get_system_user()
            assert list(revision_complete_author_message.recipients.all()) == [assigned_article.correspondence_author]
            assert revision_complete_author_message.subject == subject_revisions_complete_receipt_subject
        if confirm_version:
            assert revision.author_note == "author_note_confirm"
        else:
            assert revision.author_note == "author_note_edit"


@pytest.mark.parametrize(
    "decision",
    (
        ArticleWorkflow.Decisions.MINOR_REVISION,
        ArticleWorkflow.Decisions.MAJOR_REVISION,
        ArticleWorkflow.Decisions.TECHNICAL_REVISION,
    ),
)
@pytest.mark.django_db
def test_author_submit_checklist(
    assigned_article: submission_models.Article,
    fake_request: HttpRequest,
    decision: str,
):
    """
    Author must submit the checklist before submitting a revision.
    """
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=WjsEditorAssignment.objects.get_current(assigned_article).editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()
    revision = EditorRevisionRequest.objects.get(article=assigned_article)

    base_form_data = {
        "author_note": "author_note",
        "confirm_title": "on",
        "confirm_styles": "on",
        "confirm_blind": "on",
        "confirm_cover": "on",
    }
    for field in ["confirm_title", "confirm_styles", "confirm_blind", "confirm_cover"]:
        form_data = base_form_data.copy()
        form_data.pop(field)
        form = EditorRevisionRequestEditForm(data=form_data, instance=revision)
        assert not form.is_valid()
        assert form.check_for_potential_errors()


@pytest.mark.parametrize(
    "decision1,decision2",
    (
        (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.Decisions.MINOR_REVISION),
        (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.Decisions.MAJOR_REVISION),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.Decisions.TECHNICAL_REVISION),
        (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.Decisions.TECHNICAL_REVISION),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, ArticleWorkflow.Decisions.MINOR_REVISION),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, ArticleWorkflow.Decisions.MAJOR_REVISION),
    ),
)
@pytest.mark.django_db
def test_handle_multiple_revision_request_with_author_submission(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    decision1: str,
    decision2: str,
):
    """
    A second editor revision can be created after the author has submitted the first revision.
    """
    editor_user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor_user

    form_data = {
        "decision": decision1,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=editor_user,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()
    revision = EditorRevisionRequest.objects.get(article=assigned_article)

    if decision1 == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        # submit technical revision
        form_class = model_forms.modelform_factory(
            submission_models.Article,
            fields=ArticleRevisionUpdate.meta_data_fields,
        )
        form_data = {
            "title": "title",
            "abstract": "abstract",
        }
        form = form_class(data=form_data, instance=assigned_article)
        assert form.is_valid()
        form.save()

    form_data = {
        "author_note": "author_note",
        "confirm_title": "on",
        "confirm_styles": "on",
        "confirm_blind": "on",
        "confirm_cover": "on",
    }
    form = EditorRevisionRequestEditForm(data=form_data, instance=revision)
    assert form.is_valid()
    form.save()

    author = assigned_article.correspondence_author
    handler = AuthorHandleRevision(revision=revision, form_data=form_data, user=author, request=fake_request)
    handler.run()
    assigned_article.refresh_from_db()

    form_data = {
        "decision": decision2,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=editor_user,
        request=fake_request,
    )
    handle.run()
    new_revision = EditorRevisionRequest.objects.filter(article=assigned_article).last()
    assigned_article.refresh_from_db()
    assigned_article.articleworkflow.refresh_from_db()
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.TO_BE_REVISED
    assert new_revision.type == decision2


@pytest.mark.parametrize(
    "decision1,decision2",
    (
        (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.Decisions.MINOR_REVISION),
        (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.Decisions.MAJOR_REVISION),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.Decisions.TECHNICAL_REVISION),
        (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.Decisions.TECHNICAL_REVISION),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, ArticleWorkflow.Decisions.MINOR_REVISION),
        (ArticleWorkflow.Decisions.TECHNICAL_REVISION, ArticleWorkflow.Decisions.MAJOR_REVISION),
    ),
)
@pytest.mark.django_db
def test_handle_multiple_revision_request_no_author_submission(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    decision1: str,
    decision2: str,
):
    """
    A second editor revision can never be created.

    I.e., when the paper is under revision by the author, the editor cannot ask another revision,
    no matter what kind of revision (major, minor, technical).
    """
    editor_user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor_user

    form_data = {
        "decision": decision1,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=editor_user,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    form_data = {
        "decision": decision2,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": localtime(now()).date() + datetime.timedelta(days=7),
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=editor_user,
        request=fake_request,
    )
    with pytest.raises(ValidationError):
        handle.run()


@pytest.mark.django_db
def test_handle_withdraw_review_assignment(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
):
    """
    If the editor request a revision, pending review assignment are marked as withdrawn.
    """
    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = jcom_user
    submitted_review = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=jcom_user,
        assigned_article=assigned_article,
    )
    _submit_review(submitted_review, fake_request)
    accepted_review = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=jcom_user,
        assigned_article=assigned_article,
    )
    accepted_review.date_accepted = now()
    accepted_review.save()
    declined_review = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=jcom_user,
        assigned_article=assigned_article,
    )
    declined_review.date_declined = now()
    declined_review.date_accepted = None
    declined_review.is_complete = True
    declined_review.save()
    # Ensure initial data is consistent
    # review_assignment is not accepted by the user
    # submitted_review is accepted and submitted
    # accepted_review is accepted and not submitted
    # declined_review is declined
    assert assigned_article.reviewassignment_set.all().count() == 4
    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=False).count() == 2
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 1
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 2

    fake_request.user = section_editor
    form_data = {
        "decision": ArticleWorkflow.Decisions.MINOR_REVISION,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    mail.outbox = []
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    # All review assignment are closed
    # review_assignment is withdrawn
    # submitted_review is accepted and submitted
    # accepted_review is withdrawn
    # declined_review is declined
    assert assigned_article.reviewassignment_set.all().count() == 4
    assert assigned_article.reviewassignment_set.filter(decision="withdrawn").count() == 2
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 4
    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=False).count() == 2
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 1


@pytest.mark.django_db
def test_article_snapshot_on_revision_request(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
):
    """
    If the editor requests a revision, title, abstract and kwds are "saved" into article_history.
    """
    for __ in range(3):
        assigned_article.keywords.add(Keyword.objects.create(word=fake_factory.word()))
    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor

    fake_request.user = section_editor
    form_data = {
        "decision": ArticleWorkflow.Decisions.MINOR_REVISION,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    mail.outbox = []
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()
    revision = EditorRevisionRequest.objects.get(article=assigned_article)
    assert revision.article_history["title"] == assigned_article.title
    assert revision.article_history["abstract"] == assigned_article.abstract
    assert revision.article_history["keywords"] == list(assigned_article.keywords.values_list("word", flat=True))


@pytest.mark.django_db
def test_handle_editor_decision_check_conditions(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: review_models.ReviewForm,
):
    """
    If the HandleDecision is triggered by a non editor, an exception is raised and article is not updated.
    """

    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 0
    jcom_user.add_account_role("section-editor", assigned_article.journal)
    fake_request.user = jcom_user
    form_data = {
        "decision": ArticleWorkflow.Decisions.ACCEPT,
        "decision_editor_report": "random message",
    }
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=jcom_user,
        request=fake_request,
    )
    with pytest.raises(ValidationError, match="Decision conditions not met"):
        handle.run()
    assigned_article.refresh_from_db()
    assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert not EditorDecision.objects.filter(
        workflow=assigned_article.articleworkflow,
        review_round=assigned_article.articleworkflow.article.current_review_round_object(),
    ).exists()
    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 0


@pytest.mark.parametrize(
    "postpone_date",
    (
        -1001,  # certainly in the past
        -5,  # before the current due date
        0,  # today
        1,  # tomorrow
        10,  # in the future
        settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD + 1,  # too far in the future
    ),
)
@pytest.mark.django_db
def test_postpone_due_date(
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    fake_request: HttpRequest,
    postpone_date: int,
):
    """
    PostponeReviewerDueDate service postpones the due date of a review assignment.

    Different conditions are tested:

     - date is in the past, the due date is not changed and an error is raised.
     - date is today, the due date is not changed and an error is raised.
     - date is tomorrow, the due date is changed and a message is created and sent.
     - date is in the future, the due date is changed and a message is created and sent.
     - date is too far in the future, the due date is changed and two messages are created and sent.
    """
    # reset messages from article fixture processing
    Message.objects.all().delete()
    review_assignment.refresh_from_db()
    mail.outbox = []
    eo_user = get_eo_user(assigned_article)

    fake_request.user = review_assignment.editor
    initial_date_due = review_assignment.date_due
    form_data = {
        "date_due": initial_date_due + datetime.timedelta(days=postpone_date),
    }
    service = PostponeReviewerDueDate(
        assignment=review_assignment,
        editor=review_assignment.editor,
        user=review_assignment.editor,
        form_data=form_data,
        request=fake_request,
        original_due_date=initial_date_due,
    )
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(review_assignment),
        object_id=review_assignment.pk,
    )
    reminder_dates = {r[0]: r[1] for r in reminders.values_list("code", "date_due")}
    date_diff = form_data["date_due"] - initial_date_due
    if postpone_date < -1000:
        with pytest.raises(ValueError):
            service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == initial_date_due
        assert Message.objects.count() == 0
        assert len(mail.outbox) == 0
        return
    if postpone_date < 1:
        # The logic actually allows to change date due in the past
        service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == initial_date_due + datetime.timedelta(days=postpone_date)
        assert Message.objects.count() == 1
        assert len(mail.outbox) == 1
    elif postpone_date < settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD:
        service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == initial_date_due + datetime.timedelta(days=postpone_date)
        assert Message.objects.count() == 1
        assert Message.objects.filter(recipients__pk=review_assignment.reviewer.pk).count() == 1
        assert Message.objects.filter(recipients__pk=eo_user.pk).count() == 0
        assert len(mail.outbox) == 1
        updated_reminder_dates = {r[0]: r[1] for r in reminders.values_list("code", "date_due")}
        for reminder in updated_reminder_dates.keys():
            assert updated_reminder_dates[reminder] == reminder_dates[reminder] + date_diff
    else:
        service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == initial_date_due + datetime.timedelta(days=postpone_date)
        assert Message.objects.count() == 2
        assert Message.objects.filter(recipients__pk=review_assignment.reviewer.pk).count() == 1
        assert Message.objects.filter(recipients__pk=eo_user.pk).count() == 1
        assert len(mail.outbox) == 2
        updated_reminder_dates = {r[0]: r[1] for r in reminders.values_list("code", "date_due")}
        for reminder in updated_reminder_dates.keys():
            assert updated_reminder_dates[reminder] == reminder_dates[reminder] + date_diff


@pytest.mark.parametrize(
    "postpone_date",
    (
        -1001,  # certainly in the past
        -5,  # before the current due date
        0,  # today
        1,  # tomorrow
        10,  # in the future
    ),
)
@pytest.mark.django_db
def test_postpone_revision_due_date(
    assigned_article: submission_models.Article,
    editor_revision: EditorRevisionRequest,
    fake_request: HttpRequest,
    postpone_date: int,
):
    """
    PostponeRevisionRequestDueDate service postpones the due date of a revision request.

    Different conditions are tested:

     - date is in the past, the due date is not changed and an error is raised.
     - date is today, the due date is not changed and an error is raised.
     - date is tomorrow, the due date is changed and a message is created and sent.
     - date is in the future, the due date is changed and a message is created and sent.
     - date is too far in the future, the due date is changed and two messages are created and sent.
    """
    # reset messages from article fixture processing
    Message.objects.all().delete()
    editor_revision.refresh_from_db()
    mail.outbox = []
    eo_user = get_eo_user(assigned_article)
    editor_revision.date_due = localtime(now()).date()
    editor_revision.save()

    fake_request.user = editor_revision.editor
    initial_date_due = editor_revision.date_due
    form_data = {
        "date_due": initial_date_due + datetime.timedelta(days=postpone_date),
    }
    service = PostponeRevisionRequestDueDate(
        revision_request=editor_revision,
        form_data=form_data,
        request=fake_request,
        original_due_date=initial_date_due,
    )
    reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(editor_revision),
        object_id=editor_revision.pk,
    )
    reminder_dates = {r[0]: r[1] for r in reminders.values_list("code", "date_due")}
    date_diff = form_data["date_due"] - initial_date_due
    if postpone_date < 1:
        with pytest.raises(ValidationError):
            service.run()
        editor_revision.refresh_from_db()
        assert editor_revision.date_due == initial_date_due
        assert Message.objects.count() == 0
        assert len(mail.outbox) == 0
        return
    else:
        service.run()
        editor_revision.refresh_from_db()
        assert editor_revision.date_due == initial_date_due + datetime.timedelta(days=postpone_date)
        assert Message.objects.count() == 1
        assert Message.objects.filter(recipients__pk=assigned_article.correspondence_author.pk).count() == 1
        assert Message.objects.filter(recipients__pk=eo_user.pk).count() == 0
        assert len(mail.outbox) == 1
        updated_reminder_dates = {r[0]: r[1] for r in reminders.values_list("code", "date_due")}
        for reminder in updated_reminder_dates.keys():
            assert updated_reminder_dates[reminder] == reminder_dates[reminder] + date_diff


@pytest.mark.parametrize("actor_role", ("Reviewer", "Editor"))
@pytest.mark.django_db
def test_actors_postpone_due_date(
    assigned_article: submission_models.Article,
    review_assignment: review_models.ReviewAssignment,
    fake_request: HttpRequest,
    actor_role: str,
):
    """
    Test that this action can be performed by the editor and the reviewer and that in both scenarios the actor of the
    message is the user who performed the action.
    """
    Message.objects.all().delete()
    review_assignment.refresh_from_db()
    mail.outbox = []

    fake_request.user = review_assignment.editor

    form_data = {
        "date_due": review_assignment.date_due + datetime.timedelta(days=5),
    }

    if actor_role == "Reviewer":
        actor_user = review_assignment.reviewer
    elif actor_role == "Editor":
        actor_user = review_assignment.editor

    PostponeReviewerDueDate(
        assignment=review_assignment,
        editor=review_assignment.editor,
        user=actor_user,
        form_data=form_data,
        request=fake_request,
        original_due_date=review_assignment.date_due,
    ).run()

    assert len(mail.outbox) == 1
    assert Message.objects.first().actor == actor_user


@pytest.mark.django_db
def test_past_assignment(
    assigned_article: Article,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    fake_request: HttpRequest,
):
    """
    Past assignment is created when editor declines and assignment and the review round are migrated.

    Test timeline:

    - fixture: article is submitted and assigned to section_editor
    - t1: review round 2 is created
    - t2: review round 3 is created
    - t3: section editor declines assignment
      - state is changed to EDITOR_TO_BE_SELECTED
      - past assignment is created
      - r 1..3 assigned to past assignment
    - t4: editor_2 is assigned to the article
      - state is changed to EDITOR_SELECTED
    - t5: review round 4 is created
    - t6: editor_2 declines assignment
      - state is changed to EDITOR_TO_BE_SELECTED
      - past assignment is created
      - r 3,4 assigned to past assignment
        - r3 is the last round of the past assignment and so the first the new editor is allowed to see
    - t7: editor_3 is assigned to the article

    """
    fake_request.user = section_editor.janeway_account
    editor_2 = create_jcom_user("editor_2")
    editor_2.add_account_role("section-editor", assigned_article.journal)
    editor_3 = create_jcom_user("editor_3")
    editor_3.add_account_role("section-editor", assigned_article.journal)
    assignment_1 = WjsEditorAssignment.objects.get_current(assigned_article)

    t1 = now() + datetime.timedelta(days=1)
    t2 = t1 + datetime.timedelta(days=2)
    t3 = t2 + datetime.timedelta(days=2)
    t4 = t3 + datetime.timedelta(days=2)
    t5 = t4 + datetime.timedelta(days=2)
    t6 = t5 + datetime.timedelta(days=2)
    t7 = t6 + datetime.timedelta(days=2)

    # Preparing the history for section_editor
    r1 = review_models.ReviewRound.objects.get(article=assigned_article, round_number=1)
    with freezegun.freeze_time(t1):
        r2 = CreateReviewRound(assignment=assignment_1).run()
    with freezegun.freeze_time(t2):
        r3 = CreateReviewRound(assignment=assignment_1).run()

    with freezegun.freeze_time(t3):
        service = HandleEditorDeclinesAssignment(
            editor=section_editor.janeway_account,
            assignment=assignment_1,
            request=fake_request,
            form_data={"decline_reason": "other"},
        )
        service.run()
        assigned_article.refresh_from_db()
        assigned_article.articleworkflow.refresh_from_db()
        assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        assert PastEditorAssignment.objects.filter(editor=section_editor.janeway_account).count() == 1
        past_assignment_1 = PastEditorAssignment.objects.get(editor=section_editor.janeway_account)
        assert r1 in past_assignment_1.review_rounds.all()
        assert r2 in past_assignment_1.review_rounds.all()
        assert r3 in past_assignment_1.review_rounds.all()

    fake_request.user = editor_2.janeway_account
    with freezegun.freeze_time(t4):
        assignment_2 = AssignToEditor(
            editor=editor_2.janeway_account,
            article=assigned_article,
            request=fake_request,
        ).run()
        assigned_article.articleworkflow.refresh_from_db()
        assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    with freezegun.freeze_time(t5):
        r4 = CreateReviewRound(assignment=assignment_2).run()

    with freezegun.freeze_time(t6):
        service = HandleEditorDeclinesAssignment(
            editor=editor_2.janeway_account,
            assignment=assignment_2,
            request=fake_request,
            form_data={"decline_reason": "other"},
        )
        service.run()
        assigned_article.articleworkflow.refresh_from_db()
        assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
        assert PastEditorAssignment.objects.filter(editor=editor_2.janeway_account).count() == 1
        past_assignment_2 = PastEditorAssignment.objects.get(editor=editor_2.janeway_account)
        assert r3 in past_assignment_2.review_rounds.all()
        assert r4 in past_assignment_2.review_rounds.all()

    fake_request.user = editor_3.janeway_account
    with freezegun.freeze_time(t7):
        assignment_3 = AssignToEditor(
            editor=editor_3.janeway_account,
            article=assigned_article,
            request=fake_request,
        ).run()
        assigned_article.articleworkflow.refresh_from_db()
        assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    # All editors that saw this article, should be able
    # - to access the article itself
    # - to access their own assignments

    # Editor 1
    assert PermissionChecker()(
        assigned_article.articleworkflow,
        section_editor.janeway_account,
        assigned_article,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )
    assert PermissionChecker()(
        assigned_article.articleworkflow,
        section_editor.janeway_account,
        past_assignment_1,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )

    # Editor 2
    assert PermissionChecker()(
        assigned_article.articleworkflow,
        editor_2.janeway_account,
        assigned_article,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )
    assert PermissionChecker()(
        assigned_article.articleworkflow,
        editor_2.janeway_account,
        past_assignment_2,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )

    # Editor 3
    assert PermissionChecker()(
        assigned_article.articleworkflow,
        editor_3.janeway_account,
        assigned_article,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )
    assert PermissionChecker()(
        assigned_article.articleworkflow,
        editor_3.janeway_account,
        assignment_3,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "send_reviewer_notification,approved_assignment", [(True, True), (False, True), (True, False), (False, False)]
)
def test_deassign_reviewer(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: WorkflowReviewAssignment,
    send_reviewer_notification: bool,
    approved_assignment: bool,
):
    """
    When reviewer is deassigned, the assignment is deleted and messages are created.

    If send_reviewer_notification is True, the reviewer is notified.
    For any value of approved_assignment, reviewer reminders are deleted. The flag is not really used to assert clauses
    as we want to check any review reminder, but we want to use it to prepare data differently.
    """
    if approved_assignment:
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            request=fake_request,
            form_data={
                "reviewer_decision": "1",
                "accept_gdpr": True,
                "additional_comments": "Additional comments",
                "date_due": now().date() + datetime.timedelta(days=21),
            },
            token="",
        ).run()
    # reset messages from article fixture processing
    Message.objects.all().delete()
    review_assignment.refresh_from_db()
    mail.outbox = []
    run = DeselectReviewer(
        assignment=review_assignment,
        actor=review_assignment.editor,
        request=fake_request,
        send_reviewer_notification=send_reviewer_notification,
        form_data={"notification_subject": "subject", "notification_body": "body"},
    ).run()
    reviewer = review_assignment.reviewer
    assert run
    assert review_assignment.is_complete
    assert review_assignment.decision == "withdrawn"

    assert Message.objects.count() == 1

    if send_reviewer_notification:
        assert len(mail.outbox) == 1  # email has been sent to reviewer
        assert mail.outbox[0].to == [review_assignment.reviewer.email]
        assert Message.objects.filter(recipients__pk=reviewer.pk).count() == 1
        assert Message.objects.filter(recipients__isnull=True).count() == 0
    else:
        # no email has been sent, but the Message still has the reviewer as recipient
        assert len(mail.outbox) == 0
        assert Message.objects.filter(recipients__pk=reviewer.pk).count() == 1
        assert Message.objects.filter(recipients__isnull=True).count() == 0
    # Reminders are modified
    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(review_assignment),
        object_id=review_assignment.pk,
        code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
    ).exists()
    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(review_assignment),
        object_id=review_assignment.pk,
        code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
    ).exists()
    assert Reminder.objects.filter(code__in=EditorShouldSelectReviewerReminderManager.reminders.keys()).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("extra_assignment_state", ["declined", "accepted", "completed", "pending"])
def test_deassign_reviewer_existing_assignment(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: WorkflowReviewAssignment,
    review_form: review_models.ReviewForm,
    normal_user: JCOMProfile,
    extra_assignment_state: bool,
):
    """
    When reviewer is deassigned created reminders depend on existing assignments.
    """
    # extra assignment in
    extra_assignment = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=normal_user,
        assigned_article=assigned_article,
    )
    if extra_assignment_state == "declined":
        EvaluateReview(
            assignment=extra_assignment,
            reviewer=extra_assignment.reviewer,
            editor=extra_assignment.editor,
            request=fake_request,
            form_data={"reviewer_decision": "0", "accept_gdpr": True, "additional_comments": "Additional comments"},
            token="",
        ).run()
    elif extra_assignment_state == "accepted":
        EvaluateReview(
            assignment=extra_assignment,
            reviewer=extra_assignment.reviewer,
            editor=extra_assignment.editor,
            request=fake_request,
            form_data={
                "reviewer_decision": "1",
                "accept_gdpr": True,
                "additional_comments": "Additional comments",
                "date_due": now().date() + datetime.timedelta(days=21),
            },
            token="",
        ).run()
    elif extra_assignment_state == "completed":
        report_form = get_report_form(fake_request.journal.code)
        rf = report_form(
            data=jcom_report_form_data, review_assignment=extra_assignment, request=fake_request, submit_final=True
        )
        assert rf.is_valid()
        SubmitReview(
            assignment=extra_assignment,
            submit_final=True,
            form=rf,
            request=fake_request,
        ).run()
    # reset messages from article fixture processing
    Message.objects.all().delete()
    review_assignment.refresh_from_db()
    mail.outbox = []
    run = DeselectReviewer(
        assignment=review_assignment,
        actor=review_assignment.editor,
        request=fake_request,
        send_reviewer_notification=False,  # ⇦ don't send the email!
        form_data={"notification_subject": "subject", "notification_body": "body"},
    ).run()
    reviewer = review_assignment.reviewer
    assert run
    assert review_assignment.is_complete
    assert review_assignment.decision == "withdrawn"

    assert Message.objects.count() == 1
    assert Message.objects.filter(recipients__pk=reviewer.pk).count() == 1
    assert Message.objects.filter(recipients__isnull=True).count() == 0
    assert len(mail.outbox) == 0  # no email has been sent
    # Reminders are modified
    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(review_assignment),
        object_id=review_assignment.pk,
        code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
    ).exists()
    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(review_assignment),
        object_id=review_assignment.pk,
        code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
    ).exists()
    if extra_assignment_state == "declined":
        assert Reminder.objects.filter(code__in=EditorShouldSelectReviewerReminderManager.reminders.keys()).exists()
    else:
        assert not Reminder.objects.filter(
            code__in=EditorShouldSelectReviewerReminderManager.reminders.keys()
        ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("approved_assignment", [True, False])
def test_deassign_reviewer_no_editor(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: WorkflowReviewAssignment,
    editors: List[JCOMProfile],
    approved_assignment: bool,
):
    """
    If editor is not assigned to the article action is rejected.
    """
    if approved_assignment:
        EvaluateReview(
            assignment=review_assignment,
            reviewer=review_assignment.reviewer,
            editor=review_assignment.editor,
            request=fake_request,
            form_data={
                "reviewer_decision": "1",
                "accept_gdpr": True,
                "additional_comments": "Additional comments",
                "date_due": now().date() + datetime.timedelta(days=21),
            },
            token="",
        ).run()
    # reset messages from article fixture processing
    Message.objects.all().delete()
    mail.outbox = []
    with pytest.raises(ValueError):
        DeselectReviewer(
            assignment=review_assignment,
            actor=editors[0],
            request=fake_request,
            send_reviewer_notification=True,
            form_data={"notification_subject": "subject", "notification_body": "body"},
        ).run()
    review_assignment.refresh_from_db()
    assert not review_assignment.is_complete
    # Reminders are not modified
    if approved_assignment:
        assert Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.pk,
            code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
        ).exists()
    else:
        assert Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(review_assignment),
            object_id=review_assignment.pk,
            code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
        ).exists()
    assert not Reminder.objects.filter(code__in=EditorShouldSelectReviewerReminderManager.reminders.keys()).exists()


@pytest.mark.django_db
def test_assign_different_editor(
    assigned_article: Article,
    normal_user: JCOMProfile,
    eo_user: Account,
    fake_request: HttpRequest,
    create_jcom_user: Callable,
    review_form,
):
    """
    Assigned editor can be changed by EO.

    When switching editor, RA and reminders are updated silently to the new editor.
    """
    normal_user.add_account_role("section-editor", assigned_article.journal)
    current_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    form_data = {
        "selected_editor": normal_user.pk,
        "state": assigned_article.articleworkflow.state,
        "note_for_past_editor": "custom deassign message",
        "note_for_new_editor": "custom assign message",
    }
    reviewer1 = create_jcom_user("reviewer1")
    reviewer2 = create_jcom_user("reviewer2")
    accepted_ra = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=reviewer1,
        assigned_article=assigned_article,
    )
    EvaluateReview(
        assignment=accepted_ra,
        reviewer=accepted_ra.reviewer,
        editor=accepted_ra.editor,
        request=fake_request,
        form_data={
            "reviewer_decision": "1",
            "accept_gdpr": True,
            "additional_comments": "Additional comments",
            "date_due": now().date() + datetime.timedelta(days=21),
        },
        token="",
    ).run()
    pending_ra = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=reviewer2,
        assigned_article=assigned_article,
    )
    assert accepted_ra.editor == current_editor
    assert pending_ra.editor == current_editor
    editors = Account.objects.get_editors_with_keywords(assigned_article, current_editor)
    assert current_editor not in editors
    assert normal_user.janeway_account in editors
    assert (
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(accepted_ra),
            object_id=accepted_ra.pk,
            code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
        ).count()
        == 2
    )
    assert (
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(pending_ra),
            object_id=pending_ra.pk,
            code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
        ).count()
        == 3
    )
    form = SupervisorAssignEditorForm(
        data=form_data,
        user=eo_user,
        request=fake_request,
        instance=assigned_article.articleworkflow,
        selectable_editors=editors,
    )
    form.is_valid()
    form.save()
    assigned_article.refresh_from_db()
    assignment = WjsEditorAssignment.objects.get_current(assigned_article.articleworkflow)
    assert assignment.editor == normal_user.janeway_account
    accepted_ra.refresh_from_db()
    pending_ra.refresh_from_db()
    assert accepted_ra.editor == normal_user.janeway_account
    assert pending_ra.editor == normal_user.janeway_account

    def assert_reminder_recipient(filtered_reminders, count, check_body=True):
        assert filtered_reminders.count() == count
        if check_body:
            for reminder in filtered_reminders:
                assert reminder.recipient.full_name() in reminder.message_body

    assert_reminder_recipient(
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(accepted_ra),
            object_id=accepted_ra.pk,
            code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
            recipient=normal_user,
        ),
        1,
    )
    assert_reminder_recipient(
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(accepted_ra),
            object_id=accepted_ra.pk,
            code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
            recipient=reviewer1,
        ),
        1,
        False,
    )
    assert_reminder_recipient(
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(pending_ra),
            object_id=pending_ra.pk,
            code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
            actor=normal_user,
        ),
        2,
        False,
    )
    assert_reminder_recipient(
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(pending_ra),
            object_id=pending_ra.pk,
            code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
            recipient=normal_user,
        ),
        1,
    )

    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(accepted_ra),
        object_id=accepted_ra.pk,
        code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
        recipient=current_editor,
    ).exists()
    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(accepted_ra),
        object_id=accepted_ra.pk,
        code__in=ReviewerShouldWriteReviewReminderManager.reminders.keys(),
        actor=current_editor,
    ).exists()

    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(pending_ra),
        object_id=pending_ra.pk,
        code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
        recipient=current_editor,
    ).exists()
    assert not Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(pending_ra),
        object_id=pending_ra.pk,
        code__in=ReviewerShouldEvaluateAssignmentReminderManager.reminders.keys(),
        actor=current_editor,
    ).exists()
    assert Message.objects.count() == 6
    # messages 1-4 are generated from the data setup part of the test
    removed_editor_msg = Message.objects.all()[4]
    assert list(removed_editor_msg.recipients.all()) == [current_editor]
    editor_unassignment_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_unassign_editor",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "article": assigned_article,
        },
        template_is_setting=True,
    )
    assert removed_editor_msg.subject == editor_unassignment_subject
    assert removed_editor_msg.read_by_eo
    assert removed_editor_msg.recipients.all().count() == 1
    assert MessageRecipients.objects.get(message=removed_editor_msg, recipient=current_editor).read

    assigned_editor_msg = Message.objects.all()[5]
    assert list(assigned_editor_msg.recipients.all()) == [normal_user.janeway_account]
    editor_assignment_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="editor_assignment_manual_subject",
        journal=assigned_article.journal,
        request=fake_request,
        context={},
        template_is_setting=True,
    )
    assert assigned_editor_msg.subject == editor_assignment_subject
    assert assigned_editor_msg.read_by_eo
    assert assigned_editor_msg.recipients.all().count() == 1
    assert MessageRecipients.objects.get(message=assigned_editor_msg, recipient=normal_user.janeway_account).read


@pytest.mark.django_db
def test_assign_new_editor(
    article: Article, normal_user: JCOMProfile, eo_user: Account, fake_request: HttpRequest, review_settings
):
    """Editor can be assigned by EO to an article without prior assignees."""
    normal_user.add_account_role("section-editor", article.journal)
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()
    form_data = {
        "selected_editor": normal_user.pk,
        "state": article.articleworkflow.state,
        "note_for_new_editor": "test note",
    }
    editors = Account.objects.get_editors_with_keywords(article)
    assert normal_user.janeway_account in editors
    # TODO: refactor SupervisorAssignEditorForm to use the given "user"
    #       instead of the "request.user"
    fake_request.user = eo_user.janeway_account
    form = SupervisorAssignEditorForm(
        data=form_data,
        user=eo_user,
        request=fake_request,
        instance=article.articleworkflow,
        selectable_editors=editors,
    )
    form.is_valid()
    form.save()
    article.refresh_from_db()
    assignment = WjsEditorAssignment.objects.get_current(article.articleworkflow)
    assert assignment.editor == normal_user.janeway_account
    assert Message.objects.count() == 1
    msg = Message.objects.first()
    assert list(msg.recipients.all()) == [normal_user.janeway_account]
    message_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="editor_assignment_manual_subject",
        journal=article.journal,
        request=fake_request,
        context={},
        template_is_setting=True,
    )
    assert msg.subject == message_subject
    assert msg.read_by_eo
    assert msg.recipients.all().count() == 1
    assert MessageRecipients.objects.get(message=msg, recipient=normal_user.janeway_account).read
    assert msg.actor == eo_user.janeway_account


@pytest.mark.django_db
def test_open_appeal(rejected_article: Article, normal_user: JCOMProfile, eo_user: Account, fake_request: HttpRequest):
    """EO opens an appeal."""
    assert Message.objects.all().count() == 0
    normal_user.add_account_role("section-editor", rejected_article.journal)
    appeal_revision_days = get_setting(
        setting_group_name="wjs_review",
        setting_name="default_author_appeal_revision_days",
        journal=rejected_article.journal,
    ).process_value()
    date_due = now().date() + datetime.timedelta(days=appeal_revision_days)
    form_data = {
        "editor": normal_user.pk,
        "state": rejected_article.articleworkflow.state,
    }
    fake_request.user = eo_user
    form = OpenAppealForm(
        data=form_data,
        request=fake_request,
        instance=rejected_article.articleworkflow,
    )
    rejected_article.authors.add(normal_user.janeway_account)
    assert normal_user.janeway_account not in form.fields["editor"].queryset
    rejected_article.authors.remove(normal_user.janeway_account)
    form.is_valid()
    form.save()
    rejected_article.refresh_from_db()
    assert rejected_article.articleworkflow.state == ArticleWorkflow.ReviewStates.UNDER_APPEAL
    assignment = WjsEditorAssignment.objects.get_current(rejected_article.articleworkflow)
    revision_request = EditorRevisionRequest.objects.filter(article=rejected_article).last()
    assert assignment.editor == normal_user.janeway_account
    assert revision_request.editor == eo_user.janeway_account
    assert revision_request.date_due == date_due
    assert Message.objects.all().count() == 1
    message = Message.objects.first()
    assert message.read_by_eo is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "fixture_article",
    [
        "article",
        "assigned_article",
        "assigned_article_with_reviewer",
        "accepted_article",
        "ready_for_typesetter_article",
        "assigned_to_typesetter_article",
        "stage_proofing_article",
        "rfp_article",
        "under_appeal_article",
    ],
)
def test_author_withdraws_preprint(
    fixture_article,
    request,
    fake_request: HttpRequest,
    review_settings,
):
    """Check if author can Withdraw manuscript in different scenarios."""
    article = request.getfixturevalue(fixture_article)
    incomplete_submission = article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION
    under_appeal_state = article.articleworkflow.state == ArticleWorkflow.ReviewStates.UNDER_APPEAL
    fake_request.user = article.correspondence_author
    form_data = {
        "notification_body": "Test body",
    }
    # only assigned_article assigned_article_with_reviewer fixtures have reminders
    if fixture_article in ("assigned_article", "assigned_article_with_reviewer"):
        assert Reminder.objects.all().exists()
    else:
        assert not Reminder.objects.all().exists()
    form = WithdrawPreprintForm(
        data=form_data,
        request=fake_request,
        instance=article.articleworkflow,
        initial={"notification_subject": "Test subject"},
    )

    form.is_valid()
    form.save()
    article.refresh_from_db()
    if incomplete_submission:
        assert WjsEditorAssignment.objects.get_all(article).count() == 0
    else:
        assert WjsEditorAssignment.objects.get_all(article).count() == 1
    if under_appeal_state:
        assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.REJECTED
    else:
        assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.WITHDRAWN

    for assignment in article.reviewassignment_set.all():
        assert assignment.is_complete

    # No reminder survives the article withdrawal
    # We are testing a simplified case here, as we should filter on the reminder linked to the related objects of the
    # article. but as we only have 1 article, it's redundant and simplify test code a lot
    assert not Reminder.objects.all().exists()


@pytest.mark.django_db
def test_author_submits_after_appeal(under_appeal_article: Article, fake_request: HttpRequest):
    """An author can submit a new version after an appeal."""
    fake_request.user = under_appeal_article.correspondence_author

    revision_request = EditorRevisionRequest.objects.get(article=under_appeal_article)
    assignment = WjsEditorAssignment.objects.get_current(article=under_appeal_article)
    form_data = {
        "author_note": "author_note",
        "confirm_title": "on",
        "confirm_styles": "on",
        "confirm_blind": "on",
        "confirm_cover": "on",
    }

    service = AuthorHandleRevision(
        revision=revision_request,
        form_data=form_data,
        user=fake_request.user,
        request=fake_request,
    )
    service.run()
    under_appeal_article.refresh_from_db()
    assert under_appeal_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    content_type = ContentType.objects.get_for_model(under_appeal_article)

    # We expect only one message to the editor and it is author-read by EO and editord
    messages = Message.objects.filter(
        content_type=content_type,
        object_id=under_appeal_article.pk,
        recipients=assignment.editor,
    )
    assert messages.count() == 1
    message = messages.get()
    assert "has appealed against rejection" in message.body
    assert message.read_by_eo
    assert message.recipients.count() == 1
    assert MessageRecipients.objects.filter(
        message=message,
        recipient=assignment.editor,
        read=True,
    ).exists()


@pytest.mark.django_db
def test_write_new_note(
    article: Article,
    normal_user: JCOMProfile,
    eo_user: Account,
):
    """Notes are created setting a specific flag when initializing the form."""
    form = MessageForm(
        actor=normal_user.janeway_account,
        target=article,
        initial={"recipients": [normal_user]},
        note=True,
        data={
            "actor": normal_user.janeway_account,
            "subject": "subject",
            "body": "body",
        },
    )
    assert form.is_valid()
    msg = form.save()
    assert msg.message_type == Message.MessageTypes.NOTE


@pytest.mark.django_db
def test_write_edit_note(
    article: Article,
    normal_user: JCOMProfile,
    jcom_user: JCOMProfile,
    eo_user: Account,
):
    """
    On editing notes, the original actor is not updated.

    This is especially relevant for EO notes because they are shared, but it's a general behavior.
    """
    form = MessageForm(
        actor=normal_user.janeway_account,
        target=article,
        initial={"recipients": [normal_user]},
        note=True,
        data={
            "actor": normal_user.janeway_account,
            "subject": "subject",
            "body": "body",
        },
    )
    assert form.is_valid()
    msg = form.save()
    assert msg.message_type == Message.MessageTypes.NOTE
    assert msg.actor == normal_user.janeway_account
    assert msg.subject == "subject"
    assert msg.body == "body"

    form = MessageForm(
        actor=jcom_user.janeway_account,
        target=article,
        initial={"recipients": [normal_user]},
        note=True,
        instance=msg,
        data={
            "actor": normal_user.janeway_account,
            "subject": "subject2",
            "body": "body2",
        },
    )
    assert form.is_valid()
    msg = form.save()
    assert msg.actor == normal_user.janeway_account
    assert msg.subject == "subject2"
    assert msg.body == "body2"


@pytest.mark.django_db
def test_last_user_note(
    article: Article,
    normal_user: JCOMProfile,
    section_editor: JCOMProfile,
    eo_user: Account,
    create_note: Callable,
    create_user_message: Callable,
    fake_request,
):
    """last_user_note templatetag only returns notes ignoring messages."""
    note = create_note(
        actor=normal_user.janeway_account,
        target=article,
        subject="normal_user note",
        body="body",
    )
    editor_note = create_note(
        actor=section_editor.janeway_account,
        target=article,
        subject="section_editor note",
        body="body",
    )
    create_user_message(
        actor=normal_user.janeway_account,
        target=article,
        subject="subject",
        body="body",
        recipients=[eo_user],
        message_type=Message.MessageTypes.USER,
    )
    create_user_message(
        actor=normal_user.janeway_account,
        target=article,
        subject="subject",
        body="body",
        recipients=[eo_user],
        message_type=Message.MessageTypes.SYSTEM,
    )
    create_user_message(
        actor=normal_user.janeway_account,
        target=article,
        subject="subject",
        body="body",
        recipients=[eo_user],
        message_type=Message.MessageTypes.NOTE,
    )
    fake_request.user = normal_user.janeway_account
    context = {
        "request": fake_request,
    }
    assert last_user_note(context, article) == note
    assert last_user_note(context, article, normal_user.janeway_account) == note
    assert not last_user_note(context, article, eo_user)

    fake_request.user = section_editor.janeway_account
    context = {
        "request": fake_request,
    }
    assert last_user_note(context, article) == editor_note
    assert last_user_note(context, article, section_editor.janeway_account) == editor_note
    assert not last_user_note(context, article, eo_user)


@pytest.mark.django_db
def test_last_eo_note(
    article: Article,
    normal_user: JCOMProfile,
    section_editor: JCOMProfile,
    eo_user: Account,
    create_note: Callable,
    create_user_message: Callable,
):
    """last_eo_note templatetag only returns eo notes ignoring messages and other user messages."""
    create_note(
        actor=normal_user.janeway_account,
        target=article,
        subject="normal_user note",
        body="body",
    )
    create_note(
        actor=section_editor.janeway_account,
        target=article,
        subject="section_editor note",
        body="body",
    )
    eo_note = create_note(
        actor=eo_user,
        target=article,
        subject="eo_user note",
        body="body",
    )
    create_user_message(
        actor=normal_user.janeway_account,
        target=article,
        subject="subject",
        body="body",
        recipients=[eo_user],
        message_type=Message.MessageTypes.USER,
    )
    create_user_message(
        actor=normal_user.janeway_account,
        target=article,
        subject="subject",
        body="body",
        recipients=[eo_user],
        message_type=Message.MessageTypes.SYSTEM,
    )
    create_user_message(
        actor=normal_user.janeway_account,
        target=article,
        subject="subject",
        body="body",
        recipients=[eo_user],
        message_type=Message.MessageTypes.NOTE,
    )
    assert last_eo_note(article) == eo_note


@pytest.mark.parametrize(
    ("all_kwds", "article_kwds", "good_ones", "bad_ones"),
    [
        (
            ["a, b", "a", "b", "c"],
            ["a, b", "a", "b", "c"],
            {"a, b", "c"},
            {"a", "b"},
        ),
        (
            ["a, b", "a", "b", "c"],
            ["a", "b", "c"],
            {"a, b", "c"},
            {"a", "b"},
        ),
        (  # Article 1484
            [
                "Informal learning",
                "Science education",
                "Bridging research, practice and teaching",
                "Diversity, equity, inclusion and accessibility in science communication",
                "Policy-making, communication and governance of science",
                "Professionalism, professional development and teaching in science communication",
                "Professionalism, professional development and training in science communication",
                "Science and technology, art and literature",
                # Erroneously split kwds:
                "Bridging research",
                "practice and teaching",
                "Diversity",
                "equity",
                "inclusion and accessibility in science communication",
                "Policy-making",
                "communication and governance of science",
                "Professionalism",
                "professional development and teaching in science communication",
                "professional development and training in science communication",
                "Science and technology",
                "art and literature",
            ],
            [
                "Informal learning",
                "Science education",
                "Diversity",
                "equity",
                "inclusion and accessibility in science communication",
            ],
            {
                "Informal learning",
                "Science education",
                "Diversity, equity, inclusion and accessibility in science communication",
            },
            {
                "Diversity",
                "equity",
                "inclusion and accessibility in science communication",
            },
        ),
    ],
)
@pytest.mark.django_db
def test_reunite_divided_kwds(
    all_kwds: list[str],
    article_kwds: list[str],
    good_ones: set[str],
    bad_ones: set[str],
):
    """Test reunite_divided_kwds."""
    assert not Keyword.objects.exists()
    for word in all_kwds:
        Keyword.objects.create(word=word)

    good, bad = reunite_divided_kwds(Keyword.objects.filter(word__in=article_kwds))

    good_kwds = set(Keyword.objects.filter(id__in=good).values_list("word", flat=True))
    assert good_kwds == good_ones

    bad_kwds = set(Keyword.objects.filter(id__in=bad).values_list("word", flat=True))
    assert bad_kwds == bad_ones


@pytest.mark.django_db
def test_metadatafromtex_get_data(
    article_with_keywords: Article,
):
    """
    Document assumptions and basic working of MetadataFromTeX service.

    MetadataFromTeX pre-processe data received from JA, and expects an article to have at least
    - some authors
    - some kwds
    - an abstract

    """
    article = article_with_keywords
    author = article.owner
    service = MetadataFromTeX(workflow=article.articleworkflow)
    mock_data = {
        "abstract": "a\nb",
        "keywords": list(article.keywords.all().values_list("word", flat=True)),
        "authors_data": [
            {
                "fullname": author.full_name(),
                "first_name": author.first_name,
                "surname": author.last_name,
                "email": author.email,
                "orcid": author.orcid,
            },
        ],
    }
    with mock.patch.object(service, "get_raw_data", return_value=mock_data):
        data = service.get_data()
        assert data["abstract_adapted"] == "a b"

        # Interesting fact: the system does not fail even if the authors are not ordered
        #                   but, of course, the order is not guaranteed
        assert not ArticleAuthorOrder.objects.exists()
        assert set(
            data["authors_db"].values_list("id", flat=True),
        ) == set(
            article.authors.all().values_list("id", flat=True),
        )
        assert len(data["authors_errors"]) == 0
        assert len(data["authors_map"]) == 1
        assert data["authors_map"][0].account_id == author.pk
        assert not data["authors_map"][0].similar_accounts
        assert not data["authors_map"][0].must_be_created

        assert list(
            data["kwds_db"].values_list("id", flat=True),
        ) == list(
            data["kwds_db_raw"].values_list("id", flat=True),
        )
        assert list(
            data["kwds_db"].values_list("id", flat=True),
        ) == list(
            data["kwds_tex"].values_list("id", flat=True),
        )
        assert list(
            data["kwds_db"].values_list("id", flat=True),
        ) == list(
            article.keywords.all().values_list("id", flat=True),
        )


@pytest.mark.django_db
def test_sync_texdb(
    article_with_keywords: Article,
):
    """Sync title, abstract, kwds and authors. Simplest cases."""
    article = article_with_keywords
    article.title = "old title"
    article.abstract = "old abstract"
    article.save()

    # this fixture-article has a co-author...
    assert article.authors.count() == 2
    # ...and three kwds
    assert article.keywords.count() == 3

    author = article.owner
    new_keyword = article.journal.keywords.first()
    service = MetadataFromTeX(workflow=article.articleworkflow)
    mock_data = {
        "title": "new title",
        "abstract": "new abstract",
        # We are going to keep only one kwd:
        "keywords": [new_keyword.word],
        # We are going to drop the co-author and keep only one author:
        "authors_data": [
            {
                "fullname": author.full_name(),
                "first_name": author.first_name,
                "surname": author.last_name,
                "email": author.email,
                "orcid": author.orcid,
            },
        ],
    }
    with mock.patch.object(service, "get_raw_data", return_value=mock_data):
        service.update_titleabstract()
        article.refresh_from_db()
        assert article.title == "new title"
        assert article.abstract == "new abstract"

        service.update_authors()
        article.refresh_from_db()
        assert set(article.authors.all().values_list("id", flat=True)) == {author.pk}

        service.update_keywords()
        article.refresh_from_db()
        assert set(article.keywords.all().values_list("id", flat=True)) == {new_keyword.pk}
