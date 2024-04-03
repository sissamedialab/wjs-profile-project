import datetime
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import reverse
from django.utils.timezone import now
from faker import Faker
from review import models as review_models
from review.models import ReviewAssignment, ReviewForm
from submission import models as submission_models
from submission.models import Keyword
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token, render_template_from_setting

from .. import communication_utils
from ..events.handlers import on_revision_complete
from ..logic import (
    AdminActions,
    AssignToEditor,
    AssignToReviewer,
    EvaluateReview,
    HandleDecision,
    InviteReviewer,
    PostponeReviewerDueDate,
)
from ..models import ArticleWorkflow, EditorDecision, EditorRevisionRequest, Message
from ..plugin_settings import STAGE
from .test_helpers import _create_review_assignment, _submit_review

fake_factory = Faker()


@pytest.mark.django_db
def test_assign_to_editor(
    review_settings,
    fake_request: HttpRequest,
    director: JCOMProfile,
    section_editor: JCOMProfile,
    article: submission_models.Article,
):
    """An editor can be assigned to an article and objects states are updated."""
    fake_request.user = director.janeway_account
    article.stage = "Unsubmitted"
    article.save()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    article.articleworkflow.save()

    service = AssignToEditor(
        article=article,
        editor=section_editor.janeway_account,
        request=fake_request,
    )
    assert article.editorassignment_set.count() == 0
    assert article.reviewround_set.count() == 0

    workflow = service.run()
    assert workflow.article == article
    article.refresh_from_db()
    assert article.stage == "Assigned"
    assert article.editorassignment_set.count() == 1
    assert article.editorassignment_set.first().editor == section_editor.janeway_account
    assert article.reviewround_set.count() == 1
    assert article.reviewround_set.filter(round_number=1).count() == 1
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    # Check messages
    assert Message.objects.count() == 1
    message_to_editor = Message.objects.first()
    review_in_review_url = fake_request.journal.site_url(
        path=reverse(
            "review_in_review",
            kwargs={"article_id": article.pk},
        ),
    )
    editor_assignment_message = render_template_from_setting(
        setting_group_name="email",
        setting_name="editor_assignment",
        journal=article.journal,
        request=fake_request,
        context={
            "article": article,
            "request": fake_request,
            "editor": section_editor.janeway_account,
            "review_in_review_url": review_in_review_url,
        },
        template_is_setting=True,
    )
    assert message_to_editor.body == editor_assignment_message
    assert review_in_review_url in message_to_editor.body
    assert message_to_editor.message_type == "Verbose"
    assert list(message_to_editor.recipients.all()) == [section_editor.janeway_account]


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
    assert article.editorassignment_set.count() == 0

    with pytest.raises(ValueError, match="Invalid state transition"):
        service.run()
    article.refresh_from_db()
    assert article.editorassignment_set.count() == 0
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

    subject_review_assignment = get_setting(
        "email_subject",
        "subject_review_assignment",
        assigned_article.journal,
    ).processed_value

    assert len(user_emails) == 1
    assert len(editor_emails) == 1
    assert subject_review_assignment in user_emails[0].subject
    assert f"User {eo_user} executed Request to review" in editor_emails[0].subject


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
    assert not assignment.author_note_visible

    subject_review_assignment = get_setting(
        "email_subject",
        "subject_review_assignment",
        assigned_article.journal,
    ).processed_value
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
    assert subject_review_assignment in emails[0].subject
    assert "You have been invited" not in emails[0].body
    assert acceptance_url in emails[0].body
    assert "random message" in emails[0].body
    # Check messages
    assert Message.objects.count() == 1
    message_to_invited_user = Message.objects.first()
    assert message_to_invited_user.subject == subject_review_assignment
    assert "random message" in message_to_invited_user.body
    assert acceptance_url in message_to_invited_user.body
    assert "You have been invited" not in message_to_invited_user.body
    assert message_to_invited_user.message_type == "Verbose"
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
        "decision_internal_note": "random internal message",
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
        setting_group_name="wjs_review",
        setting_name="review_decision_revision_request_subject",
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
        setting_group_name="wjs_review",
        setting_name="review_decision_revision_request_body",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "article": assigned_article,
            "request": fake_request,
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
    # Check message
    assert Message.objects.count() == 1
    message_to_correspondence_author = Message.objects.get()
    assert message_to_correspondence_author.subject == revision_request_message_subject
    assert message_to_correspondence_author.body == revision_request_message_body
    assert message_to_correspondence_author.message_type == "Verbose"
    assert message_to_correspondence_author.actor == section_editor.janeway_account
    assert list(message_to_correspondence_author.recipients.all()) == [assigned_article.correspondence_author]
    # Check email
    assert len(mail.outbox) == 1
    mail_to_correspondence_author = mail.outbox[0]
    assert revision_request_message_subject in mail_to_correspondence_author.subject
    assert revision_request_message_body in mail_to_correspondence_author.body
    assert mail_to_correspondence_author.from_email == settings.DEFAULT_FROM_EMAIL
    assert mail_to_correspondence_author.from_email != section_editor.email
    assert list(mail_to_correspondence_author.recipients()) == [assigned_article.correspondence_author.email]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "revision_type,previous_assignment",
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
    previous_assignment: bool,
):
    """Context after completed revision request is marked with revision status flags."""
    fake_request.user = section_editor.janeway_account
    form_data = {
        "decision": revision_type,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
        "withdraw_notice": "notice",
        "date_due": now().date() + datetime.timedelta(days=7),
    }
    if previous_assignment:
        _create_review_assignment(
            fake_request=fake_request,
            reviewer_user=normal_user,
            assigned_article=assigned_article,
        )

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
    on_revision_complete(revision=revision_request)

    acceptance_due_date = now().date() + datetime.timedelta(days=7)
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
    assert context["already_reviewed"] == previous_assignment


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
    assert assignment.workflowreviewassignment.author_note_visible

    subject_review_assignment = get_setting(
        "email_subject",
        "subject_review_assignment",
        assigned_article.journal,
    ).processed_value
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
    assert subject_review_assignment in emails[0].subject
    assert "is a diamond open access" in emails[0].body
    assert acceptance_url in emails[0].body
    assert "random message" in emails[0].body
    # Check messages
    assert Message.objects.count() == 1
    message_to_invited_user = Message.objects.first()
    assert message_to_invited_user.subject == subject_review_assignment
    assert "random message" in message_to_invited_user.body
    assert acceptance_url in message_to_invited_user.body
    assert "is a diamond open access" in message_to_invited_user.body
    assert message_to_invited_user.message_type == "Verbose"
    assert message_to_invited_user.actor == section_editor.janeway_account
    assert list(message_to_invited_user.recipients.all()) == [invited_user.janeway_account]


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_handle_accept_invite_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
    accept_gdpr: bool,
):
    """If the user accepts the invitation, assignment is accepted and user is confirmed if they accept GDPR."""

    invited_user = review_assignment.reviewer
    assignment = assigned_article.reviewassignment_set.first()

    evaluate_data = {"reviewer_decision": "1", "accept_gdpr": accept_gdpr}

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
        assert Message.objects.count() == 2
        # Message related to the reviewer accepting the assignment
        message = Message.objects.last()
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [section_editor.janeway_account]
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_accept_acknowledgement",
            journal=assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_accept_acknowledgement",
            journal=assignment.article.journal,
            request=fake_request,
            context={
                "article": assignment.article,
                "request": fake_request,
                "review_assignment": assignment,
                "review_url": reverse("wjs_review_review", kwargs={"assignment_id": assignment.id}),
            },
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
    assert assignment.date_due == now().date() + datetime.timedelta(default_review_days)

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
    review_assignment: ReviewAssignment,
    accept_gdpr: bool,
):
    """If the user declines the invitation, assignment is declined and user is confirmed if they accept GDPR."""

    invited_user = review_assignment.reviewer
    assignment = assigned_article.reviewassignment_set.first()
    fake_request.GET = {"access_code": assignment.access_code}

    evaluate_data = {"reviewer_decision": "0", "accept_gdpr": accept_gdpr}

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
    assert Message.objects.count() == 2
    # Message related to the reviewer declining the assignment
    message = Message.objects.last()
    assert message.actor == invited_user
    assert list(message.recipients.all()) == [section_editor.janeway_account]
    message_subject = get_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_decline_acknowledgement",
        journal=assignment.article.journal,
    ).processed_value
    message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="review_decline_acknowledgement",
        journal=assignment.article.journal,
        request=fake_request,
        context={
            "article": assignment.article,
            "request": fake_request,
            "review_assignment": assignment,
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": assignment.id}),
        },
        template_is_setting=True,
    )
    assert message.subject == message_subject
    assert message.body == message_body
    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)

    assert invited_user.is_active == accept_gdpr
    assert invited_user.jcomprofile.gdpr_checkbox == accept_gdpr
    assert bool(invited_user.jcomprofile.invitation_token) != accept_gdpr
    assert not assignment.date_accepted
    assert assignment.date_declined
    assert assignment.is_complete
    assert assignment.date_due == now().date() + datetime.timedelta(default_review_days)


@pytest.mark.django_db
def test_handle_update_due_date_in_evaluate_review_in_the_future(
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
):
    """If the user decides to postpone the due date, and it's in the future with respect to the current due date."""

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    assert review_assignment.date_due == now().date() + datetime.timedelta(default_review_days)
    new_date_due = review_assignment.date_due + datetime.timedelta(days=1)

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
def test_handle_update_due_date_in_evaluate_review_in_the_past(
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
):
    """If the user decides to postpone the due date, and it's in the past with respect to the current due date."""

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    assert review_assignment.date_due == now().date() + datetime.timedelta(default_review_days)
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
    message_to_reviewer = Message.objects.get(subject__startswith="Request to review")
    assert "random message" in message_to_reviewer.body
    assert message_to_reviewer.message_type == "Verbose"
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
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
    submit_final: bool,
):
    """
    If the reviewer submits a review, reviewassignment is marked as complete (and accepted if not).
    """

    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(is_complete=False).count() == 1
    fake_request.user = review_assignment.reviewer
    _submit_review(review_assignment, review_form, fake_request, submit_final)
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
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
    submit_final: bool,
):
    """
    If the reviewer submits a review, two messages are created, and sent to the editor and
    the reviewer.
    """

    fake_request.user = review_assignment.reviewer
    assert Message.objects.count() == 1
    _submit_review(review_assignment, review_form, fake_request, submit_final)
    assert Message.objects.count() == 3
    message_to_the_reviewer = (
        Message.objects.filter(recipients__pk=review_assignment.reviewer.pk).order_by("created").last()
    )
    reviewer_message_subject = get_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_complete_reviewer_acknowledgement",
        journal=assigned_article.journal,
    ).processed_value
    assert message_to_the_reviewer.subject == reviewer_message_subject
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
    assert message_to_the_reviewer.body == reviewer_message_body
    assert message_to_the_reviewer.message_type == Message.MessageTypes.VERBOSE
    message_to_the_editor = Message.objects.get(recipients__pk=review_assignment.editor.pk)
    editor_message_subject = get_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_complete_acknowledgement",
        journal=assigned_article.journal,
    ).processed_value
    assert message_to_the_editor.subject == editor_message_subject
    editor_message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="review_complete_acknowledgement",
        journal=assigned_article.journal,
        request=fake_request,
        context={
            "review_assignment": review_assignment,
            "article": assigned_article,
        },
        template_is_setting=True,
    )
    assert message_to_the_editor.body == editor_message_body
    assert message_to_the_editor.message_type == Message.MessageTypes.VERBOSE


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
    review_form: ReviewForm,
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
            setting_name="requeue_article_message",
            journal=article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        assert message.subject == requeue_article_subject
        assert message.body == requeue_article_message
        assert message.message_type == message.MessageTypes.SYSTEM
        assert not message.recipients.all().exists()
        # no email because there is no recipient
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
    review_form: ReviewForm,
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
    review_form: ReviewForm,
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
    review_assignment: ReviewAssignment,
    jcom_user: JCOMProfile,
    eo_user: JCOMProfile,
    review_form: ReviewForm,
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
        "decision_internal_note": "random internal message",
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
        withdrawn_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="review_withdraw_subject",
            journal=assigned_article.journal,
        ).processed_value
        withdrawn_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_withdraw_body",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_context,
            template_is_setting=True,
        )
        # Check the message
        assert not_suitable_message.actor == eo_user.janeway_account
        assert list(not_suitable_message.recipients.all()) == [assigned_article.correspondence_author]
        assert not_suitable_message.subject == not_suitable_message_subject
        assert not_suitable_message.body == not_suitable_message_body
        assert withdrawn_message.actor == eo_user.janeway_account
        assert list(withdrawn_message.recipients.all()) == [review.reviewer]
        assert withdrawn_message.subject == withdrawn_message_subject
        assert withdrawn_message.body == withdrawn_message_body
        # Check the mail
        assert len(mail.outbox) == 2
        # In HandleDecision.run,
        # - first we _withdraw_unfinished_review_requests
        # - then we _log_not_suitable
        # so the _last_ email is the one about the "not suitable" notification to the author
        not_suitable_mail = mail.outbox[1]
        assert not_suitable_message_subject in not_suitable_mail.subject
        assert not_suitable_message_body in not_suitable_mail.body
        withdrawn_mail = mail.outbox[0]
        assert withdrawn_message_subject in withdrawn_mail.subject
        assert withdrawn_message_body in withdrawn_mail.body


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
    review_assignment: ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: ReviewForm,
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
        "decision_internal_note": "random internal message",
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
    review_assignment: ReviewAssignment,
    eo_user: JCOMProfile,
    review_form: ReviewForm,
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
        "decision_internal_note": "random internal message",
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
    review_assignment: ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: ReviewForm,
    decision: str,
    final_state: str,
):
    """
    If the editor makes a decision, article.stage is set to the next workflow stage if decision is final
    and articleworkflow.state is updated according to the decision.
    """
    editor_user = assigned_article.editorassignment_set.first().editor
    fake_request.user = jcom_user
    review_2 = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=jcom_user,
        assigned_article=assigned_article,
    )
    _submit_review(review_2, review_form, fake_request)
    # Ensure initial data is consistent: review_2 is accepted and complete, review_assignment is not
    assert assigned_article.reviewassignment_set.all().count() == 2
    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 0
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 1

    fake_request.user = editor_user
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
        "withdraw_notice": "notice",
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
    handle.run()
    assigned_article.refresh_from_db()

    # We had an incomplete review assignment; when the editor makes a decision (except for technical revision),
    # these are withdrawn and the reviewer informed.
    # Test this case for all the states at once to ease detailed testing.
    message_template_context = {
        "article": assigned_article,
        "request": fake_request,
        "decision": form_data["decision"],
        "user_message_content": form_data["decision_editor_report"],
        "withdraw_notice": form_data["withdraw_notice"],
        "skip": False,
    }
    review_withdraw_message_subject = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="review_withdraw_subject",
        journal=assigned_article.journal,
        request=fake_request,
        context=message_template_context,
        template_is_setting=True,
    )
    review_withdraw_message_body = render_template_from_setting(
        setting_group_name="wjs_review",
        setting_name="review_withdraw_body",
        journal=assigned_article.journal,
        request=fake_request,
        context=message_template_context,
        template_is_setting=True,
    )
    if decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assert Message.objects.count() == 1
    elif decision not in (ArticleWorkflow.Decisions.TECHNICAL_REVISION,):
        assert Message.objects.count() == 2
        withdrawn_review_message = Message.objects.order_by("created").first()
        assert withdrawn_review_message.subject == review_withdraw_message_subject
        assert withdrawn_review_message.body == review_withdraw_message_body
        assert withdrawn_review_message.message_type == Message.MessageTypes.VERBOSE
        assert len(mail.outbox) == 2
        withdrawn_review_mail = mail.outbox[0]
        assert review_withdraw_message_subject in withdrawn_review_mail.subject
        assert review_withdraw_message_body in withdrawn_review_mail.body

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
            "user_message_content": form_data["decision_editor_report"],
            "withdraw_notice": form_data["withdraw_notice"],
            "skip": False,
        }
        revision_request_message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_revision_request_subject",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_template_context,
            template_is_setting=True,
        )
        revision_request_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_revision_request_body",
            journal=assigned_article.journal,
            request=fake_request,
            context=message_template_context,
            template_is_setting=True,
        )
        # Check the messages - withdraw message testsed above
        assert Message.objects.count() == 1
        revision_request_message = Message.objects.order_by("created").last()
        assert revision_request_message.subject == revision_request_message_subject
        assert revision_request_message.body == revision_request_message_body
        assert revision_request_message.message_type == Message.MessageTypes.VERBOSE
        # Check the emails - withdraw message testsed above
        assert len(mail.outbox) == 1
        revision_request_mail = mail.outbox[0]
        assert revision_request_message_subject in revision_request_mail.subject
        assert revision_request_message_body in revision_request_mail.body
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
                "user_message_content": form_data["decision_editor_report"],
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        # Check the message
        assert Message.objects.count() == 1
        accept_message = Message.objects.get()
        assert accept_message.actor == editor_user
        assert list(accept_message.recipients.all()) == [assigned_article.correspondence_author]
        assert accept_message.subject == accept_message_subject
        assert accept_message.body == accept_message_body
        assert accept_message.message_type == Message.MessageTypes.VERBOSE
        # Check that one email is sent by us (and not by Janeway)
        assert len(mail.outbox) == 1
        accept_mail = mail.outbox[0]
        assert accept_message_subject in accept_mail.subject
        assert accept_message_body in accept_mail.body
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
                "user_message_content": form_data["decision_editor_report"],
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        # Check the message
        assert Message.objects.count() == 1
        assert not_suitable_message.actor == editor_user
        assert list(not_suitable_message.recipients.all()) == [assigned_article.correspondence_author]
        assert not_suitable_message.subject == not_suitable_message_subject
        assert not_suitable_message.body == not_suitable_message_body
        # Check the mail
        assert len(mail.outbox) == 1
        not_suitable_mail = mail.outbox[0]
        assert not_suitable_message_subject in not_suitable_mail.subject
        assert not_suitable_message_body in not_suitable_mail.body
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
                "user_message_content": form_data["decision_editor_report"],
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        # Check the message
        assert Message.objects.count() == 1
        reject_message = Message.objects.get()
        assert reject_message.actor == editor_user
        assert list(reject_message.recipients.all()) == [assigned_article.correspondence_author]
        assert reject_message.subject == reject_message_subject
        assert reject_message.body == reject_message_body
        assert reject_message.message_type == Message.MessageTypes.VERBOSE
        # Check that one email is sent by us (and not by Janeway)
        assert len(mail.outbox) == 1
        reject_mail = mail.outbox[0]
        assert reject_message_subject in reject_mail.subject
        assert reject_message_body in reject_mail.body
    elif decision == ArticleWorkflow.Decisions.TECHNICAL_REVISION:
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVIEW
        assert assigned_article.articleworkflow.state == final_state
        # Prepare subject and body
        technical_revision_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="technical_revision_subject",
            journal=assigned_article.journal,
        ).processed_value
        technical_revision_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="technical_revision_body",
            journal=assigned_article.journal,
            request=fake_request,
            context={
                "article": assigned_article,
                "request": fake_request,
                "revision": None,
                "decision": form_data["decision"],
                "user_message_content": form_data["decision_editor_report"],
                "withdraw_notice": form_data["withdraw_notice"],
                "skip": False,
            },
            template_is_setting=True,
        )
        # Check the message
        assert Message.objects.count() == 1
        technical_revision_message = Message.objects.get()
        assert technical_revision_message.actor == editor_user
        assert list(technical_revision_message.recipients.all()) == [assigned_article.correspondence_author]
        assert technical_revision_message.subject == technical_revision_message_subject
        assert technical_revision_message.body == technical_revision_message_body
        assert technical_revision_message.message_type == Message.MessageTypes.VERBOSE
        # Check that one email is sent by us (and not by Janeway)
        assert len(mail.outbox) == 1
        reject_mail = mail.outbox[0]
        assert technical_revision_message_subject in reject_mail.subject
        assert technical_revision_message_body in reject_mail.body

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
    assert editor_decision.decision_editor_report == form_data["decision_editor_report"]
    assert editor_decision.decision_internal_note == form_data["decision_internal_note"]


@pytest.mark.django_db
def test_handle_withdraw_review_assignment(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
    review_assignment: ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: ReviewForm,
):
    """
    If the editor request a revision, pending review assignment are marked as withdrawn.
    """
    section_editor = assigned_article.editorassignment_set.first().editor
    fake_request.user = jcom_user
    submitted_review = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=jcom_user,
        assigned_article=assigned_article,
    )
    _submit_review(submitted_review, review_form, fake_request)
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
        "decision_internal_note": "random internal message",
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
    review_form: ReviewForm,
):
    """
    If the editor requests a revision, title, abstract and kwds are "saved" into article_history.
    """
    for __ in range(3):
        assigned_article.keywords.add(Keyword.objects.create(word=fake_factory.word()))
    section_editor = assigned_article.editorassignment_set.first().editor

    fake_request.user = section_editor
    form_data = {
        "decision": ArticleWorkflow.Decisions.MINOR_REVISION,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
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
    review_assignment: ReviewAssignment,
    jcom_user: JCOMProfile,
    review_form: ReviewForm,
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
        "decision_internal_note": "random internal message",
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
        -5,  # in the past
        0,  # today
        1,  # tomorrow
        10,  # in the future
        settings.DAYS_CONSIDERED_FAR_FUTURE + 1,  # too far in the future
    ),
)
@pytest.mark.django_db
def test_postpone_due_date(
    assigned_article: submission_models.Article,
    review_assignment: ReviewAssignment,
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
    mail.outbox = []
    eo_user = communication_utils.get_eo_user(assigned_article)

    fake_request.user = review_assignment.editor
    initial_date_due = review_assignment.date_due
    _now = now().date()
    form_data = {
        "date_due": _now + datetime.timedelta(days=postpone_date),
    }
    service = PostponeReviewerDueDate(
        assignment=review_assignment,
        editor=review_assignment.editor,
        form_data=form_data,
        request=fake_request,
    )

    if postpone_date < 1:
        with pytest.raises(ValueError):
            service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == initial_date_due
        assert Message.objects.count() == 0
        assert len(mail.outbox) == 0
    elif postpone_date < settings.DAYS_CONSIDERED_FAR_FUTURE:
        service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == _now + datetime.timedelta(days=postpone_date)
        assert Message.objects.count() == 1
        assert Message.objects.filter(recipients__pk=review_assignment.reviewer.pk).count() == 1
        assert Message.objects.filter(recipients__pk=eo_user.pk).count() == 0
        assert len(mail.outbox) == 1
    else:
        service.run()
        review_assignment.refresh_from_db()
        assert review_assignment.date_due == _now + datetime.timedelta(days=postpone_date)
        assert Message.objects.count() == 2
        assert Message.objects.filter(recipients__pk=review_assignment.reviewer.pk).count() == 1
        assert Message.objects.filter(recipients__pk=eo_user.pk).count() == 1
        assert len(mail.outbox) == 2
