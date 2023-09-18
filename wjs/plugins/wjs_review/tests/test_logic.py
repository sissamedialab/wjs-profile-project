from datetime import timedelta

import pytest
from dateutil.utils import today
from django.core import mail
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import reverse
from faker import Faker
from review import models as review_models
from review.models import ReviewAssignment
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

from ..logic import AssignToEditor, AssignToReviewer, EvaluateReview, InviteReviewer
from ..models import ArticleWorkflow

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
            "acceptance_due_date": today() + timedelta(days=7),
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
            "acceptance_due_date": today() + timedelta(days=7),
            "message": "random message",
        },
        request=fake_request,
    )
    assert normal_user.janeway_account not in assigned_article.journal.users_with_role("reviewer")
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
            "acceptance_due_date": today() + timedelta(days=7),
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
            "acceptance_due_date": today() + timedelta(days=7),
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
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_settings,
):
    """A user can be invited and a user a review assignment must be created."""
    fake_request.user = section_editor.janeway_account

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
    gdpr_acceptance_url = assigned_article.journal.site_url(
        reverse(
            "wjs_evaluate_review",
            kwargs={"token": invitation_token, "assignment_id": assigned_article.reviewassignment_set.first().pk},
        ),
    )

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
    assert len(mail.outbox) == 1

    subject_review_assignment = get_setting(
        "email_subject",
        "subject_review_assignment",
        assigned_article.journal,
    ).processed_value
    acceptance_url = f"{gdpr_acceptance_url}?access_code={assigned_article.reviewassignment_set.first().access_code}"
    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert email.to == [invited_user.email]
    assert email.subject == f"[{assigned_article.journal.code}] {subject_review_assignment}"
    assert acceptance_url in email.body


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_handle_accept_invite_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
    review_settings,
    accept_gdpr: bool,
):
    """If the user accepts the invitation, assignment is accepted and user is confirmed if they accept GDPR."""

    invited_user = review_assignment.reviewer
    assignment = assigned_article.reviewassignment_set.first()

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

    if accept_gdpr:
        assert invited_user.is_active
        assert invited_user.jcomprofile.gdpr_checkbox
        assert not invited_user.jcomprofile.invitation_token
        assert assignment.date_accepted
        assert not assignment.date_declined
        assert not assignment.is_complete
    else:
        assert not invited_user.is_active
        assert not invited_user.jcomprofile.gdpr_checkbox
        assert invited_user.jcomprofile.invitation_token
        assert not assignment.date_accepted
        assert not assignment.date_declined
        assert not assignment.is_complete


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_handle_decline_invite_reviewer(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_assignment: ReviewAssignment,
    review_settings,
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

    assert invited_user.is_active == accept_gdpr
    assert invited_user.jcomprofile.gdpr_checkbox == accept_gdpr
    assert bool(invited_user.jcomprofile.invitation_token) != accept_gdpr
    assert not assignment.date_accepted
    assert assignment.date_declined
    assert assignment.is_complete


# TODO: test invite user fails if user already exists
# TODO: test failure in AssignToReviewer are bubbled up


@pytest.mark.django_db
def test_invite_reviewer_but_user_already_exists(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    normal_user: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
    review_settings,
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
    email = mail.outbox[0]
    assert email.to == [invited_user.email]
