from datetime import timedelta

import pytest
from dateutil.utils import today
from django.core import mail
from django.http import HttpRequest
from django.urls import reverse
from faker import Faker
from review import models as review_models
from submission import models as submission_models
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

from ..logic import AssignToEditor, AssignToReviewer, InviteReviewer
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


# TODO: test invite user fails if user already exists
# TODO: test failure in AssignToReviewer are bubbled up