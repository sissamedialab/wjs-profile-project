import datetime
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import reverse
from django.utils.timezone import now
from faker import Faker
from review import models as review_models
from review.const import EditorialDecisions
from review.models import ReviewAssignment, ReviewForm
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

from ..logic import (
    AssignToEditor,
    AssignToReviewer,
    EvaluateReview,
    HandleDecision,
    InviteReviewer,
)
from ..models import ArticleWorkflow, EditorDecision, EditorRevisionRequest
from .test_helpers import _create_review_assignment, _submit_review, get_next_workflow

fake_factory = Faker()


@pytest.mark.django_db
def test_assign_to_editor(
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
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    assignment = service.run()
    assigned_article.refresh_from_db()

    assert normal_user.janeway_account in assigned_article.journal.users_with_role("reviewer")
    assert assigned_article.stage == "Under Review"
    assert assigned_article.reviewassignment_set.count() == 1
    assert assigned_article.reviewround_set.count() == 1
    assert assigned_article.reviewround_set.filter(round_number=1).count() == 1
    # This is "delicate": the presence or absence of ReviewAssignments does not change the article's state
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert assignment.reviewer == normal_user.janeway_account
    assert assignment.editor == section_editor.janeway_account


@pytest.mark.django_db
def test_cannot_assign_to_reviewer_if_revision_requested(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """A reviewer canot be assigned if a revision request is in progress."""
    fake_request.user = section_editor.janeway_account
    form_data = {
        "decision": ArticleWorkflow.Decisions.MINOR_REVISION,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
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


@pytest.mark.django_db
def test_invite_reviewer(
    review_settings,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """A user can be invited and a user a review assignment must be created."""
    fake_request.user = section_editor.janeway_account

    journal_code = assigned_article.journal.code
    user_data = {
        "first_name": fake_factory.first_name(),
        "last_name": fake_factory.last_name(),
        "email": fake_factory.email(),
        "message": "random message",
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
    url = reverse(
        "wjs_evaluate_review",
        kwargs={"token": invitation_token, "assignment_id": assigned_article.reviewassignment_set.first().pk},
    )
    # url starts with journal code, but just in case that the tests run makes it NOT start with the journal code...
    if not url.startswith(f"/{journal_code}"):
        url = f"/{journal_code}{url}"

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
    # 1 notification to the section editor (by AssignToEditor)
    # 1 notification to the reviewer (by Janeway)
    # 1 notification to the reviewer (by InviteReviewer)
    assert len(mail.outbox) == 3
    # TODO: drop to "2" when we "silence" Janeway notifications

    subject_review_assignment = get_setting(
        "email_subject",
        "subject_review_assignment",
        assigned_article.journal,
    ).processed_value
    acceptance_url = f"{url}?access_code={assigned_article.reviewassignment_set.first().access_code}"
    # TODO: review me when we silence Janeway notifications
    assert len(mail.outbox) == 3
    emails = [m for m in mail.outbox if m.to[0] == invited_user.email]
    assert len(emails) == 2
    assert f"[{assigned_article.journal.code}] {subject_review_assignment}" in [email.subject for email in emails]
    # super fragile
    janeway_email = [email for email in emails if email.subject.startswith("[JCOM]")][0]
    assert acceptance_url in janeway_email.body


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_handle_accept_invite_reviewer(
    review_settings,
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

    # Now there is no need to add `"message": "random message"` here because when a reviewer accepts an assignment he
    # does not send any message, but MT hinted that we might want to add a message here in the future.
    evaluate_data = {"reviewer_decision": "1", "accept_gdpr": accept_gdpr}

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
    review_settings,
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
    review_settings,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
):
    """If the user decides to postpone the due date, and it's in the future with respect to the current due date."""

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    assert review_assignment.date_due.date() == now().date() + datetime.timedelta(default_review_days)
    new_date_due = review_assignment.date_due.date() + datetime.timedelta(days=1)

    evaluate_data = {"reviewer_decision": "2", "date_due": new_date_due}

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

    # check that the due date is updated
    # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date object
    assert review_assignment.date_due == new_date_due


@pytest.mark.django_db
def test_handle_update_due_date_in_evaluate_review_in_the_past(
    review_settings,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
):
    """If the user decides to postpone the due date, and it's in the past with respect to the current due date."""

    invited_user = review_assignment.reviewer
    fake_request.GET = {"access_code": review_assignment.access_code}

    default_review_days = int(get_setting("general", "default_review_days", fake_request.journal).value)
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    assert review_assignment.date_due.date() == now().date() + datetime.timedelta(default_review_days)
    new_date_due = review_assignment.date_due.date() - datetime.timedelta(days=1)

    evaluate_data = {"reviewer_decision": "2", "date_due": new_date_due}

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

    # Check that the low level logic class allows to update the due date even if it's in the past
    # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date object
    assert review_assignment.date_due == new_date_due


# TODO: test failure in AssignToReviewer are bubbled up


@pytest.mark.django_db
def test_invite_reviewer_but_user_already_exists(
    review_settings,
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
    # TODO: see notes in test_invite_reviewer() above
    assert len(mail.outbox) == 3
    janeway_email = [email for email in mail.outbox if email.subject.startswith("[JCOM]")][0]
    assert janeway_email.to == [invited_user.email]


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


@pytest.mark.parametrize(
    "decision,final_state,has_revision",
    (
        (ArticleWorkflow.Decisions.ACCEPT, ArticleWorkflow.ReviewStates.ACCEPTED, False),
        (ArticleWorkflow.Decisions.REJECT, ArticleWorkflow.ReviewStates.REJECTED, False),
        (ArticleWorkflow.Decisions.NOT_SUITABLE, ArticleWorkflow.ReviewStates.NOT_SUITABLE, False),
        (ArticleWorkflow.Decisions.MINOR_REVISION, ArticleWorkflow.ReviewStates.TO_BE_REVISED, True),
        (ArticleWorkflow.Decisions.MAJOR_REVISION, ArticleWorkflow.ReviewStates.TO_BE_REVISED, True),
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
    has_revision: bool,
):
    """
    If the editor makes a decision, article.stage is set to the next workflow stage if decision is final
    and articleworkflow.state is updated according to the decision.
    """
    section_editor = assigned_article.editorassignment_set.first().editor
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

    fake_request.user = section_editor
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "decision_internal_note": "random internal message",
    }
    if has_revision:
        form_data["date_due"] = now().date() + datetime.timedelta(days=7)
    handle = HandleDecision(
        workflow=assigned_article.articleworkflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    )
    handle.run()
    assigned_article.refresh_from_db()

    if has_revision:
        # article is kept the as ON_WORKFLOW_ELEMENT_COMPLETE event is not triggered
        assert assigned_article.stage == submission_models.STAGE_UNDER_REVISION
        assert assigned_article.articleworkflow.state == final_state
        revision = EditorRevisionRequest.objects.get(
            article=assigned_article,
            review_round=review_assignment.review_round,
        )
        assert revision.editor_note == "random message"
        assert revision.date_due == form_data["date_due"]
        assert revision.type == (
            EditorialDecisions.MINOR_REVISIONS.value
            if form_data["decision"] == ArticleWorkflow.Decisions.MINOR_REVISION
            else EditorialDecisions.MAJOR_REVISIONS.value
        )
    else:
        # article is moved to the next stage by ON_WORKFLOW_ELEMENT_COMPLETE event triggered by HandleDecision
        next_stage = get_next_workflow(assigned_article.journal)
        assert assigned_article.stage == next_stage.stage

        assert assigned_article.articleworkflow.state == final_state

    # All review assignments are marked as complete, review_assignment is automatically marked as declined
    assert assigned_article.reviewassignment_set.filter(date_accepted__isnull=True).count() == 1
    assert assigned_article.reviewassignment_set.filter(date_declined__isnull=False).count() == 1
    assert assigned_article.reviewassignment_set.filter(is_complete=True).count() == 2

    editor_decision = EditorDecision.objects.get(
        workflow=assigned_article.articleworkflow,
        review_round=assigned_article.articleworkflow.article.current_review_round_object(),
    )
    assert editor_decision.decision == decision
    assert editor_decision.decision_editor_report == form_data["decision_editor_report"]
    assert editor_decision.decision_internal_note == form_data["decision_internal_note"]


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
