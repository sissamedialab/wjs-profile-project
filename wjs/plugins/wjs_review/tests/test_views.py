import datetime
from typing import Callable, Iterable, List, Optional

import freezegun
import pytest
from core.models import Account
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponseRedirect, QueryDict
from django.template.loader import render_to_string
from django.test import override_settings
from django.test.client import Client
from django.urls import reverse
from django.utils import translation
from django.utils.formats import date_format
from django.utils.timezone import localtime, now
from journal.models import Journal
from press.models import Press
from review import models as review_models
from review.models import ReviewAssignment, ReviewForm, ReviewRound
from submission import models as submission_models
from submission.models import Article
from utils import setting_handler
from utils.setting_handler import get_setting

from wjs.jcom_profile import constants
from wjs.jcom_profile.models import JCOMProfile, StaffKeyword, StaffWorkloadParameters
from wjs.jcom_profile.tests.conftest import _journal_factory
from wjs.jcom_profile.utils import generate_token, render_template_from_setting

from ..forms import DecisionForm
from ..logic import AssignToEditor, HandleDecision, HandleEditorDeclinesAssignment
from ..models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    Message,
    PastEditorAssignment,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from ..permissions import is_article_editor
from ..templatetags.wjs_articles import user_is_coauthor
from ..templatetags.wjs_review import get_editor_keywords, get_version_submission_date
from ..views import (
    ArticleDetails,
    ArticleIdToDetails,
    EditorArchived,
    EOPending,
    SelectReviewerView,
    SupervisorAssignEditor,
)


@pytest.mark.parametrize(
    "decision",
    (
        ArticleWorkflow.Decisions.MAJOR_REVISION,
        ArticleWorkflow.Decisions.MINOR_REVISION,
        ArticleWorkflow.Decisions.TECHNICAL_REVISION,
    ),
)
@pytest.mark.django_db
def test_editor_requests_revision_but_date_due_in_the_past(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    decision: ArticleWorkflow.Decisions,
):
    """An editor cannot set the minor/major/technical revision request's date_due in the past wrt today."""
    url = reverse("wjs_article_decision", args=(assigned_article.pk,))
    post_data = {
        "decision": decision.value,
        "date_due": localtime(now()).date() - datetime.timedelta(days=1),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    assert dict(response.context["form"].errors) == {"date_due": ["Date must be in the future"]}


@pytest.mark.parametrize(
    "decision,setting_name",
    (
        (ArticleWorkflow.Decisions.MAJOR_REVISION, "default_author_major_revision_days_max"),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "default_author_minor_revision_days_max"),
    ),
)
@pytest.mark.django_db
def test_editor_requests_revision_but_date_due_too_much_in_the_future(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    decision: ArticleWorkflow.Decisions,
    setting_name: str,
):
    """An editor cannot set the minor/major revision request's date_due too much in the future wrt settings."""
    url = reverse("wjs_article_decision", args=(assigned_article.pk,))
    revision_days_max = get_setting(
        setting_group_name="wjs_review",
        setting_name=setting_name,
        journal=assigned_article.journal,
    ).processed_value
    post_data = {
        "decision": decision.value,
        "date_due": localtime(now()).date() + datetime.timedelta(days=revision_days_max + 1),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    assert dict(response.context["form"].errors) == {
        "date_due": [f"Date must be less than {revision_days_max} days in the future"]
    }


@pytest.mark.parametrize(
    "decision,setting_name",
    (
        (ArticleWorkflow.Decisions.MAJOR_REVISION, "default_author_major_revision_days_max"),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "default_author_minor_revision_days_max"),
    ),
)
@pytest.mark.django_db
def test_editor_requests_revision_date_due_is_in_the_right_range(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    decision: ArticleWorkflow.Decisions,
    setting_name: str,
):
    """An editor can set the minor/major revision request's date_due in the right range (between today + 1 and the
    relevant max value possible).
    """
    url = reverse("wjs_article_decision", args=(assigned_article.pk,))
    revision_days_max = get_setting(
        setting_group_name="wjs_review",
        setting_name=setting_name,
        journal=assigned_article.journal,
    ).processed_value
    how_many_days_in_the_future = 1
    assert how_many_days_in_the_future < revision_days_max
    post_data = {
        "decision": decision.value,
        "date_due": localtime(now()).date() + datetime.timedelta(days=how_many_days_in_the_future),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 302
    assert response.url == reverse("wjs_article_details", args=(assigned_article.pk,))


@pytest.mark.parametrize(
    "decision,days_setting_name",
    (
        (ArticleWorkflow.Decisions.MAJOR_REVISION, "default_author_major_revision_days"),
        (ArticleWorkflow.Decisions.MINOR_REVISION, "default_author_minor_revision_days"),
    ),
)
@pytest.mark.django_db
def test_editor_requests_revision_date_due_renders_iso_in_non_default_language(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    decision: ArticleWorkflow.Decisions,
    days_setting_name: str,
):
    """
    The date_due field's computed initial value must reach the browser as an ISO 8601 date.

    Regression test: Django 5.0 removed USE_L10N (now always effectively True), so the
    date_due DateInput (type="date") used to silently render in the active locale's date
    format instead of ISO ("2026-09-15"). HTML5 <input type="date"> requires an exact ISO
    value or the browser discards it, so editors using a non-English UI never saw the
    revision due date pre-filled on the make-decision page, even though the value was
    computed correctly (see test_editor_requests_revision_date_due_is_in_the_right_range
    above).
    """
    url = reverse("wjs_article_decision", args=(assigned_article.pk,))
    revision_days = get_setting(
        setting_group_name="wjs_review",
        setting_name=days_setting_name,
        journal=assigned_article.journal,
    ).processed_value
    initial_date = localtime(now()).date() + datetime.timedelta(days=revision_days)
    client.force_login(section_editor.janeway_account)
    with translation.override("fr"):
        response = client.get(url, {"decision": decision.value})
    assert f'value="{initial_date.isoformat()}"' in response.content.decode()


@pytest.mark.django_db
def test_editor_assigns_themselves_as_reviewer_gives_403_if_user_is_not_editor(
    client: Client,
    reviewer: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """A reviewer tries to use EditorAssignsThemselvesAsReviewer to assign themselves as reviewer."""
    url = reverse("wjs_editor_assigns_themselves_as_reviewer", args=(assigned_article.pk,))
    client.force_login(reviewer.janeway_account)
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_editor_assigns_themselves_as_reviewer_acceptance_due_date_in_the_past(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """An editor cannot set the acceptance_due_date in the past wrt localtime(now()).date()."""
    url = reverse("wjs_editor_assigns_themselves_as_reviewer", args=(assigned_article.pk,))
    post_data = {
        "acceptance_due_date": localtime(now()).date() - datetime.timedelta(days=1),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    assert dict(response.context["form"].errors) == {"acceptance_due_date": ["Date must be in the future"]}


@pytest.mark.django_db
def test_editor_assigns_themselves_as_reviewer_with_a_date_much_in_the_future(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_form: review_models.ReviewForm,
):
    """
    We don't have a maximum date that can be set as due date for the reviewer to complete the review.
    This is valid also for the Editor as reviewer
    """
    url = reverse("wjs_editor_assigns_themselves_as_reviewer", args=(assigned_article.pk,))
    today = localtime(now()).date()
    date_max = today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX)
    post_data = {
        "acceptance_due_date": date_max + datetime.timedelta(days=100),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200


@pytest.mark.django_db
def test_select_reviewer_queryset_for_editor(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    submitted_articles: Iterable[submission_models.Article],
):
    """An editor can only access SelectReviewer for their own articles."""
    fake_request.user = section_editor.janeway_account

    view = SelectReviewerView()
    view.request = fake_request
    qs = view.get_queryset()
    assert qs.count() == 1
    assert qs.get().article == assigned_article


@pytest.mark.django_db
def test_select_reviewer_queryset_for_non_editor(
    fake_request: HttpRequest,
    reviewer: JCOMProfile,
    assigned_article: submission_models.Article,
    submitted_articles: Iterable[submission_models.Article],
):
    """A non editor will not have any available articles."""

    fake_request.user = reviewer.janeway_account

    view = SelectReviewerView()
    view.request = fake_request
    qs = view.get_queryset()
    assert qs.count() == 0


@pytest.mark.django_db
def test_select_reviewer_raise_403_for_not_editor(
    client: Client,
    jcom_user: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """Not editors gets permission denied error when accessing SelectReviewer."""
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    client.force_login(jcom_user.janeway_account)
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_select_reviewer_raise_404_for_editor_not_assigned(
    review_settings,
    client: Client,
    section_editor: JCOMProfile,
    submitted_articles: List[submission_models.Article],
):
    """An editor is returned a 404 status for when accessing SelectReviewer for an article they are not editor for."""
    article = submitted_articles[0]
    url = reverse("wjs_select_reviewer", args=(article.pk,))
    client.force_login(section_editor.janeway_account)
    response = client.get(f"{article.journal.code}/{url}")
    assert response.status_code == 404


@pytest.mark.django_db
def test_select_reviewer_status_code_200_for_assigned_editor(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """An editor can access SelectReviewer for their own articles."""
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    client.force_login(section_editor.janeway_account)
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["workflow"] == assigned_article.articleworkflow


@pytest.mark.parametrize("review_round,expected", ((1, False), (2, True)))
@pytest.mark.django_db
def test_author_note_visible_default(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    review_round: int,
    expected: bool,
):
    """author_note_visible is False for first review round, True for the following."""
    if review_round == 2:
        ReviewRound.objects.create(article=assigned_article, round_number=review_round)
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    client.force_login(section_editor.janeway_account)
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["workflow"] == assigned_article.articleworkflow
    assert response.context["form"].initial["author_note_visible"] is expected


@pytest.mark.django_db
def test_select_reviewer_acceptance_due_date_in_the_past(
    client: Client,
    section_editor: JCOMProfile,
    reviewer: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """An editor cannot set the acceptance_due_date in the past wrt localtime(now()).date()."""
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    post_data = {
        "reviewer": reviewer.pk,
        "message": "test_select_reviewer_acceptance_due_date_in_the_past() test message",
        "acceptance_due_date": localtime(now()).date() - datetime.timedelta(days=1),
        "state": "",
        "author_note_visible": False,
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    assert dict(response.context["form"].errors) == {"acceptance_due_date": ["Date must be in the future"]}


@pytest.mark.django_db
def test_select_reviewer_acceptance_due_date_too_much_in_the_future(
    client: Client,
    section_editor: JCOMProfile,
    reviewer: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """An editor cannot set the acceptance_due_date after DEFAULT_ACCEPTANCE_DUE_DATE_MAX wrt today."""
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    today = localtime(now()).date()
    date_min = today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MIN)
    date_max = today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX)
    post_data = {
        "reviewer": reviewer.pk,
        "message": "test_select_reviewer_acceptance_due_date_in_the_past() test message",
        "acceptance_due_date": date_max + datetime.timedelta(days=1),
        "state": "",
        "author_note_visible": False,
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    errors = {
        "acceptance_due_date": [
            f"Date must be between {date_format(date_min, settings.DATE_FORMAT)} and "
            f"{date_format(date_max, settings.DATE_FORMAT)}"
        ]
    }
    assert dict(response.context["form"].errors) == errors


@pytest.mark.django_db
def test_invite_button_is_in_select_reviewer_interface(
    client: Client,
    assigned_article: submission_models.Article,
):
    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    url = reverse("wjs_select_reviewer", args=(assigned_article.articleworkflow.pk,))
    client.force_login(section_editor)
    response = client.get(url)
    invite_url = reverse("wjs_invite_reviewer", args=(assigned_article.articleworkflow.pk,))
    assert response.status_code == 200
    assert invite_url in response.content.decode()


@pytest.mark.django_db
def test_invite_function_creates_inactive_user(
    client: Client,
    assigned_article: submission_models.Article,
    review_form: ReviewForm,
    fake_request: HttpRequest,
):
    section_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    url = reverse("wjs_invite_reviewer", args=(assigned_article.articleworkflow.pk,))
    client.force_login(section_editor)
    data = {
        "first_name": "Name",
        "last_name": "Surname",
        "suffix": "Suffix",
        "email": "email@email.it",
        "message": "random message",
        "acceptance_due_date": localtime(now()).date()
        + datetime.timedelta(
            days=fake_request.journal.get_setting("wjs_review", "default_review_acceptance_days"),
        ),
    }
    response = client.post(url, data=data)
    assert response.status_code == 200

    invited_user = JCOMProfile.objects.get(email=data["email"])
    invitation_token = generate_token(data["email"], assigned_article.journal.code)
    # FIXME: This is wrong (it should be handled like in wjs_review.tests.test_login.test_invite_reviewer
    #        but it must be fixed in https://gitlab.sissamedialab.it/wjs/specs/-/issues/424
    base_gdpr_acceptance_url = reverse(
        "wjs_evaluate_review",
        kwargs={"token": invitation_token, "assignment_id": assigned_article.reviewassignment_set.first().pk},
    )
    gdpr_acceptance_url = assigned_article.journal.site_url(
        f"/{assigned_article.journal.code}{base_gdpr_acceptance_url}",
    )

    assert invited_user
    assert not invited_user.is_active
    assert not invited_user.gdpr_checkbox
    for field, _ in data.items():
        if field not in ["message", "acceptance_due_date"]:
            assert getattr(invited_user, field) == data[field]
    assert invited_user.invitation_token == invitation_token

    review_assignment_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_assignment",
        journal=assigned_article.journal,
        request=fake_request,
        context={"article": assigned_article, "reviewer": invited_user},
        template_is_setting=True,
    )
    acceptance_url = f"{gdpr_acceptance_url}?access_code={assigned_article.reviewassignment_set.first().access_code}"
    assert len(mail.outbox) == 1
    # Check messages
    assert Message.objects.count() == 1
    message_to_reviewer = Message.objects.first()
    assert review_assignment_subject == message_to_reviewer.subject
    assert "random message" in message_to_reviewer.body
    assert acceptance_url in message_to_reviewer.body
    assert message_to_reviewer.message_type == Message.MessageTypes.SYSTEM
    assert message_to_reviewer.actor == section_editor
    assert list(message_to_reviewer.recipients.all()) == [invited_user.janeway_account]


@pytest.mark.django_db
def test_wjs_evaluate_review_date_due_initial_value(
    client: Client,
    review_assignment_invited_user: ReviewAssignment,
    review_form: ReviewForm,
):
    """Check that the initial value of the EvaluateReviewForm date_due field is today() + setting."""
    # The user is irrelevant in this test, I just copy-pasted the "setup" code from another test
    invited_user = review_assignment_invited_user.reviewer
    url = reverse(
        "wjs_evaluate_review", args=(review_assignment_invited_user.pk, invited_user.jcomprofile.invitation_token)
    )
    url = f"{url}?access_code={review_assignment_invited_user.access_code}"
    response = client.get(url)
    default_review_days = get_setting(
        setting_group_name="general",
        setting_name="default_review_days",
        journal=review_assignment_invited_user.article.journal,
    ).processed_value
    initial_date = localtime(now()).date() + datetime.timedelta(days=default_review_days)
    assert response.context["form"].initial["date_due"] == initial_date


@pytest.mark.django_db
def test_wjs_evaluate_review_date_due_renders_iso_in_non_default_language(
    client: Client,
    review_assignment_invited_user: ReviewAssignment,
    review_form: ReviewForm,
):
    """
    The date_due field's computed initial value must reach the browser as an ISO 8601 date.

    Regression test: Django 5.0 removed USE_L10N (now always effectively True), so the
    date_due DateInput (type="date") used to silently render in the active locale's date
    format (e.g. "15/09/2026" under fr/it/es, "15.09.2026" under de) instead of ISO
    ("2026-09-15"). HTML5 <input type="date"> requires an exact ISO value or the browser
    discards it, so reviewers using a non-English UI never saw the due date pre-filled on
    the accept/decline invite page, even though the value was computed correctly (see
    test_wjs_evaluate_review_date_due_initial_value above).
    """
    invited_user = review_assignment_invited_user.reviewer
    url = reverse(
        "wjs_evaluate_review", args=(review_assignment_invited_user.pk, invited_user.jcomprofile.invitation_token)
    )
    url = f"{url}?access_code={review_assignment_invited_user.access_code}"
    default_review_days = get_setting(
        setting_group_name="general",
        setting_name="default_review_days",
        journal=review_assignment_invited_user.article.journal,
    ).processed_value
    initial_date = localtime(now()).date() + datetime.timedelta(days=default_review_days)
    with translation.override("fr"):
        response = client.get(url)
    assert f'value="{initial_date.isoformat()}"' in response.content.decode()


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_accept_invite(
    client: Client,
    review_assignment_invited_user: ReviewAssignment,
    review_form: ReviewForm,
    accept_gdpr: bool,
):
    """If user accepts the invitation, it's accepted only if they selects gdpr acceptance."""
    invited_user = review_assignment_invited_user.reviewer
    url = reverse(
        "wjs_evaluate_review", args=(review_assignment_invited_user.pk, invited_user.jcomprofile.invitation_token)
    )
    url = f"{url}?access_code={review_assignment_invited_user.access_code}"
    redirect_url = reverse("wjs_review_review", args=(review_assignment_invited_user.pk,))
    redirect_url = f"{redirect_url}?access_code={review_assignment_invited_user.access_code}"
    data = {"reviewer_decision": "1", "accept_gdpr": accept_gdpr, "date_due": review_assignment_invited_user.date_due}
    # Message related to the editor assignment
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    request = response.wsgi_request
    review_assignment_invited_user.refresh_from_db()
    invited_user.refresh_from_db()
    if accept_gdpr:
        # See also test_logic.test_handle_accept_invite_reviewer()
        assert Message.objects.count() == 3
        # Message related to the reviewer accepting the assignment
        message = Message.objects.get(recipients__in=[review_assignment_invited_user.editor])
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [review_assignment_invited_user.editor]
        context = {
            "article": review_assignment_invited_user.article,
            "request": request,
            "review_assignment": review_assignment_invited_user,
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": review_assignment_invited_user.id}),
        }
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_reviewer_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="reviewer_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        assert message.subject == message_subject
        assert message.body == message_body.replace("<br/>", "<br>")  # FIXME: janeway settings?
        assert response.status_code == 302
        assert response.headers["Location"] == redirect_url
        assert invited_user.is_active
        assert invited_user.jcomprofile.gdpr_checkbox
        assert not invited_user.jcomprofile.invitation_token
        assert review_assignment_invited_user.date_accepted
        assert not review_assignment_invited_user.date_declined
        assert not review_assignment_invited_user.is_complete
    else:
        # No new message created
        assert Message.objects.count() == 1
        assert "Please acknowledge the privacy policy to continue" in response.content.decode()
        assert not invited_user.is_active
        assert not invited_user.jcomprofile.gdpr_checkbox
        assert invited_user.jcomprofile.invitation_token
        assert not review_assignment_invited_user.date_accepted
        assert not review_assignment_invited_user.date_declined
        assert not review_assignment_invited_user.is_complete


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_accept_invite_date_due_in_the_future(
    client: Client,
    review_assignment_invited_user: ReviewAssignment,
    review_form: ReviewForm,
    accept_gdpr: bool,
):
    """If user accepts the invitation, it's accepted only if they selects gdpr acceptance."""
    invited_user = review_assignment_invited_user.reviewer
    url = reverse(
        "wjs_evaluate_review", args=(review_assignment_invited_user.pk, invited_user.jcomprofile.invitation_token)
    )
    url = f"{url}?access_code={review_assignment_invited_user.access_code}"
    redirect_url = reverse("wjs_review_review", args=(review_assignment_invited_user.pk,))
    redirect_url = f"{redirect_url}?access_code={review_assignment_invited_user.access_code}"
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    date_due = review_assignment_invited_user.date_due + datetime.timedelta(days=1)
    data = {"reviewer_decision": "1", "accept_gdpr": accept_gdpr, "date_due": date_due}
    # Message related to the editor assignment
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    request = response.wsgi_request
    review_assignment_invited_user.refresh_from_db()
    invited_user.refresh_from_db()

    if accept_gdpr:
        # See also test_logic.test_handle_accept_invite_reviewer()
        assert Message.objects.count() == 3
        # Message related to the reviewer accepting the assignment
        message = Message.objects.get(recipients__in=[review_assignment_invited_user.editor])
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [review_assignment_invited_user.editor]
        context = {
            "article": review_assignment_invited_user.article,
            "request": request,
            "review_assignment": review_assignment_invited_user,
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": review_assignment_invited_user.id}),
        }
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_reviewer_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="reviewer_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        assert message.subject == message_subject
        assert message.body == message_body
        assert response.status_code == 302
        assert response.headers["Location"] == redirect_url
        assert invited_user.is_active
        assert invited_user.jcomprofile.gdpr_checkbox
        assert not invited_user.jcomprofile.invitation_token
        assert review_assignment_invited_user.date_accepted
        assert not review_assignment_invited_user.date_declined
        assert not review_assignment_invited_user.is_complete
        # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date
        assert review_assignment_invited_user.date_due == date_due
    else:
        # No new message created
        assert Message.objects.count() == 1
        assert "Please acknowledge the privacy policy to continue" in response.content.decode()
        assert not invited_user.is_active
        assert not invited_user.jcomprofile.gdpr_checkbox
        assert invited_user.jcomprofile.invitation_token
        assert not review_assignment_invited_user.date_accepted
        assert not review_assignment_invited_user.date_declined
        assert not review_assignment_invited_user.is_complete
        # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date
        assert review_assignment_invited_user.date_due != date_due


@pytest.mark.parametrize("accept_gdpr", (True, False))
@pytest.mark.django_db
def test_accept_invite_but_date_due_in_the_past(
    client: Client,
    review_assignment_invited_user: ReviewAssignment,
    review_form: ReviewForm,
    accept_gdpr: bool,
):
    """If user accepts the invitation, it's accepted only if they selects gdpr acceptance."""
    invited_user = review_assignment_invited_user.reviewer
    url = reverse(
        "wjs_evaluate_review", args=(review_assignment_invited_user.pk, invited_user.jcomprofile.invitation_token)
    )
    url = f"{url}?access_code={review_assignment_invited_user.access_code}"
    # Janeway' quick_assign() sets date_due as timezone.now() + timedelta(something), so it's a datetime.datetime
    date_due = review_assignment_invited_user.date_due - datetime.timedelta(days=1)
    data = {"reviewer_decision": "1", "accept_gdpr": accept_gdpr, "date_due": date_due}
    # Message related to the editor assignment
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    review_assignment_invited_user.refresh_from_db()
    invited_user.refresh_from_db()

    assert response.status_code == 200
    assert not invited_user.is_active
    assert not invited_user.jcomprofile.gdpr_checkbox
    assert invited_user.jcomprofile.invitation_token
    assert not review_assignment_invited_user.date_accepted
    assert not review_assignment_invited_user.date_declined
    assert not review_assignment_invited_user.is_complete
    assert response.context_data["form"].errors["date_due"] == ["Date must be in the future"]

    if accept_gdpr:
        assert "Please acknowledge the privacy policy to continue" not in response.content.decode()
    else:
        assert "Please acknowledge the privacy policy to continue" in response.content.decode()
    # No new message created, in any case
    assert Message.objects.count() == 1


@pytest.mark.parametrize(
    "accept_gdpr,reason",
    ((True, ""), (True, "I don't like it"), (False, ""), (False, "I don't like it")),
)
@pytest.mark.django_db
def test_decline_invite(
    client: Client,
    review_assignment_invited_user: ReviewAssignment,
    review_form: ReviewForm,
    accept_gdpr: bool,
    reason: str,
):
    """If user declines the invitation, is activated only if accepts gdpr and declined only if it provides reason."""
    invited_user = review_assignment_invited_user.reviewer
    url = reverse(
        "wjs_evaluate_review", args=(review_assignment_invited_user.pk, invited_user.jcomprofile.invitation_token)
    )
    url = f"{url}?access_code={review_assignment_invited_user.access_code}"
    redirect_url = reverse("wjs_declined_review", args=(review_assignment_invited_user.pk,))
    redirect_url = f"{redirect_url}?access_code={review_assignment_invited_user.access_code}"
    data = {
        "reviewer_decision": "0",
        "accept_gdpr": accept_gdpr,
        "date_due": review_assignment_invited_user.date_due,
        "additional_comments": reason,
    }
    # Message related to the editor assignment
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    request = response.wsgi_request
    review_assignment_invited_user.refresh_from_db()
    invited_user.refresh_from_db()
    if not reason:
        assert response.status_code == 200
        assert "Please provide a reason for declining" in response.content.decode()
        assert not invited_user.is_active
        assert not invited_user.jcomprofile.gdpr_checkbox
        assert invited_user.jcomprofile.invitation_token
        assert not review_assignment_invited_user.date_accepted
        assert not review_assignment_invited_user.date_declined
        assert not review_assignment_invited_user.is_complete
    elif accept_gdpr:
        assert response.status_code == 302
        assert response.headers["Location"] == redirect_url
        assert invited_user.is_active
        assert invited_user.jcomprofile.gdpr_checkbox
        assert not invited_user.jcomprofile.invitation_token
        assert not review_assignment_invited_user.date_accepted
        assert review_assignment_invited_user.date_declined
        assert review_assignment_invited_user.is_complete
    else:
        assert response.status_code == 302
        assert response.headers["Location"] == redirect_url
        assert not invited_user.is_active
        assert not invited_user.jcomprofile.gdpr_checkbox
        assert invited_user.jcomprofile.invitation_token
        assert not review_assignment_invited_user.date_accepted
        assert review_assignment_invited_user.date_declined
        assert review_assignment_invited_user.is_complete

    if reason:
        # See also test_logic.test_handle_accept_invite_reviewer()
        assert Message.objects.count() == 3
        # Message related to the reviewer accepting the assignment
        message = Message.objects.get(recipients__in=[review_assignment_invited_user.editor])
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [review_assignment_invited_user.editor]
        context = {
            "article": review_assignment_invited_user.article,
            "request": request,
            "review_assignment": review_assignment_invited_user,
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": review_assignment_invited_user.id}),
            "additional_comments": reason,
        }
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_reviewer_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="reviewer_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context=context,
            template_is_setting=True,
        )
        assert message.subject == message_subject
        assert message.body == message_body
    else:
        # No new message created
        assert Message.objects.count() == 1


@pytest.mark.django_db
def test_accepted_assignment_submit(
    client: Client,
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
):
    """If user accepts the invitation, access to review submit page is granted."""
    reviewer = review_assignment.reviewer
    client.force_login(reviewer)
    url = reverse("wjs_evaluate_review", args=(review_assignment.pk,))

    response = client.get(url)
    assert response.status_code == 200

    date_due = review_assignment.date_due + datetime.timedelta(days=1)
    data = {"reviewer_decision": "1", "date_due": date_due}
    # Message from the editor to the reviewer ("User ... invited to review")
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    assert response.status_code == 302
    review_assignment.refresh_from_db()
    reviewer.refresh_from_db()

    url = reverse("wjs_review_review", args=(review_assignment.pk,))

    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_declined_assignment_submit(
    client: Client,
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
):
    """If user declines the invitation, access to review submit page is denied with 404."""
    reviewer = review_assignment.reviewer
    client.force_login(reviewer)
    url = reverse("wjs_evaluate_review", args=(review_assignment.pk,))

    response = client.get(url)
    assert response.status_code == 200

    date_due = review_assignment.date_due + datetime.timedelta(days=1)
    data = {"reviewer_decision": "0", "additional_comments": "Don't say", "date_due": date_due}
    # Message related to the editor assignment
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    assert response.status_code == 302
    review_assignment.refresh_from_db()
    reviewer.refresh_from_db()

    url = reverse("wjs_review_review", args=(review_assignment.pk,))

    response = client.get(url)
    assert response.status_code == 404


@pytest.mark.django_db
def test_double_accept_assignment(
    client: Client,
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
):
    """If user accepts the invitation, a second acceptance is silently redirected to the review submissions."""
    reviewer = review_assignment.reviewer
    client.force_login(reviewer)
    url = reverse("wjs_evaluate_review", args=(review_assignment.pk,))

    response = client.get(url)
    assert response.status_code == 200

    redirect_url = reverse("wjs_review_review", args=(review_assignment.pk,))
    date_due = review_assignment.date_due + datetime.timedelta(days=1)
    data = {"reviewer_decision": "1", "date_due": date_due}
    # Message from the editor to the reviewer ("User ... invited to review")
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    request = response.wsgi_request
    review_assignment.refresh_from_db()
    reviewer.refresh_from_db()

    # See also test_logic.test_handle_accept_invite_reviewer()
    assert Message.objects.count() == 3
    # Message related to the reviewer accepting the assignment
    message = Message.objects.get(recipients__in=[review_assignment.editor])
    assert message.actor == reviewer
    assert list(message.recipients.all()) == [review_assignment.editor]
    context = {
        "article": review_assignment.article,
        "request": request,
        "review_assignment": review_assignment,
        "review_url": reverse("wjs_review_review", kwargs={"assignment_id": review_assignment.id}),
    }
    message_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_reviewer_acknowledgement",
        journal=review_assignment.article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )
    message_body = render_template_from_setting(
        setting_group_name="email",
        setting_name="reviewer_acknowledgement",
        journal=review_assignment.article.journal,
        request=request,
        context=context,
        template_is_setting=True,
    )
    assert message.subject == message_subject
    assert message.body == message_body
    assert response.status_code == 302
    assert response.headers["Location"] == redirect_url
    assert reviewer.is_active
    assert reviewer.jcomprofile.gdpr_checkbox
    assert not reviewer.jcomprofile.invitation_token
    assert review_assignment.date_accepted
    assert not review_assignment.date_declined
    assert not review_assignment.is_complete
    # In the database ReviewAssignment.date_due is a DateField, so when loaded from the db it's a datetime.date
    assert review_assignment.date_due == date_due

    base_accept_date = review_assignment.date_accepted
    open_attempt_1 = review_assignment.date_due + datetime.timedelta(days=1)
    open_attempt_2 = review_assignment.date_due + datetime.timedelta(days=2)

    assert Message.objects.count() == 3
    initial_message = Message.objects.count()

    with freezegun.freeze_time(open_attempt_1):
        # A second acceptance will ignore the acceptance and redirect the user to the review submission
        # Redirection is triggered by both post and get
        client.force_login(reviewer)
        response = client.get(url)
        assert response.status_code == 301
        assert response.headers["Location"] == redirect_url
        # No new message is generated
        assert Message.objects.count() == initial_message
        review_assignment.refresh_from_db()
        assert review_assignment.date_accepted == base_accept_date

    with freezegun.freeze_time(open_attempt_2):
        client.force_login(reviewer)
        response = client.post(url, data=data)
        assert response.status_code == 301
        assert response.headers["Location"] == redirect_url
        review_assignment.refresh_from_db()
        reviewer.refresh_from_db()
        # No new message is generated
        assert Message.objects.count() == initial_message
        review_assignment.refresh_from_db()
        assert review_assignment.date_accepted == base_accept_date


@pytest.mark.django_db
def test_accept_declined_assignment(
    client: Client,
    review_assignment: ReviewAssignment,
    review_form: ReviewForm,
):
    """If user declines the invitation, a second attempt of decline / accept yields 404."""
    reviewer = review_assignment.reviewer
    client.force_login(reviewer)
    url = reverse("wjs_evaluate_review", args=(review_assignment.pk,))

    response = client.get(url)
    assert response.status_code == 200

    date_due = review_assignment.date_due + datetime.timedelta(days=1)
    data = {"reviewer_decision": "0", "additional_comments": "Don't say", "date_due": date_due}
    # Message related to the editor assignment
    assert Message.objects.count() == 1
    response = client.post(url, data=data)
    review_assignment.refresh_from_db()
    reviewer.refresh_from_db()

    # Original message + decline messages
    assert Message.objects.count() == 3
    assert response.status_code == 302
    assert reviewer.is_active
    assert reviewer.jcomprofile.gdpr_checkbox
    assert not reviewer.jcomprofile.invitation_token
    assert not review_assignment.date_accepted
    assert review_assignment.date_declined
    assert review_assignment.is_complete
    assert review_assignment.date_due == date_due

    base_declined_date = review_assignment.date_declined
    open_attempt_1 = review_assignment.date_due + datetime.timedelta(days=1)
    open_attempt_2 = review_assignment.date_due + datetime.timedelta(days=2)

    assert Message.objects.count() == 3
    initial_message = Message.objects.count()

    with freezegun.freeze_time(open_attempt_1):
        # A second decline will be ignored and produce a 404
        client.force_login(reviewer)
        response = client.get(url)
        assert response.status_code == 404
        # No new message is generated
        assert Message.objects.count() == initial_message
        review_assignment.refresh_from_db()
        assert review_assignment.date_declined == base_declined_date
        assert not review_assignment.date_accepted

    with freezegun.freeze_time(open_attempt_2):
        client.force_login(reviewer)
        response = client.post(url, data=data)
        assert response.status_code == 404
        review_assignment.refresh_from_db()
        reviewer.refresh_from_db()
        # No new message is generated
        assert Message.objects.count() == initial_message
        review_assignment.refresh_from_db()
        assert review_assignment.date_declined == base_declined_date
        assert not review_assignment.date_accepted


@pytest.mark.django_db
def test_user_is_coauthor(article: submission_models.Article, jcom_user: JCOMProfile):
    """user_is_coauthor check input user is coauthor."""
    article.correspondence_author = None
    article.authors.add(jcom_user.janeway_account)
    article.save()
    assert user_is_coauthor(article, jcom_user.janeway_account) is True


@pytest.mark.django_db
def test_user_is_not_coauthor_author(article: submission_models.Article, jcom_user: JCOMProfile):
    """user_is_coauthor check input user is not coauthor."""
    article.correspondence_author = jcom_user.janeway_account
    article.authors.add(jcom_user.janeway_account)
    article.save()
    assert user_is_coauthor(article, jcom_user.janeway_account) is False


@pytest.mark.django_db
def test_user_is_not_coauthor_no_author(article: submission_models.Article, jcom_user: JCOMProfile):
    """user_is_coauthor check input user is not coauthor."""
    assert user_is_coauthor(article, jcom_user.janeway_account) is False


@pytest.mark.django_db
def test_user_is_not_coauthor_anonymous(article: submission_models.Article):
    """user_is_coauthor check input user is not coauthor (anonymous)."""
    assert user_is_coauthor(article, AnonymousUser()) is None


# MOVED FROM wjs/jcom_profile/tests/test_views.py
# because needs review_settings
@pytest.mark.django_db
def test_email_are_sent_to_author_and_coauthors_after_article_submission_(
    review_settings,
    admin,
    article,
    coauthors_setting,
    director_role,
    fake_request: HttpRequest,
):
    client = Client()
    client.force_login(admin)
    url = reverse("submit_review", args=(article.pk,))
    coauthors_email = list(
        article.author_accounts.exclude(email=article.correspondence_author.email).values_list("email", flat=True),
    )

    response = client.post(url, data={"next_step": "next_step"})
    assert response.status_code == 302
    assert len(mail.outbox) == article.author_accounts.count()

    subject_of_message_to_coauthors = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="submission_coauthors_acknowledgement_subject",
        journal=article.journal,
        request=fake_request,
        context={"article": article},
        template_is_setting=True,
    )
    for m in mail.outbox:
        # Remember that emails' subjects have a prefix automatically added,
        # so we need to use `in` instead of `==`
        if subject_of_message_to_coauthors in m.subject:
            assert m.to == coauthors_email
        else:
            assert m.to == [article.correspondence_author.email]


@pytest.mark.django_db
def test_revision_file_replace(
    review_settings,
    editor_revision: EditorRevisionRequest,
):
    """Files from editorrevision are used to replace article files."""
    client = Client()
    article = editor_revision.article
    client.force_login(article.correspondence_author)
    assert article.manuscript_files.count() == 1
    assert article.supplementary_files.count() == 1
    assert article.data_figure_files.count() == 1
    assert editor_revision.manuscript_files.count() == 3
    assert editor_revision.supplementary_files.count() == 2
    assert editor_revision.data_figure_files.count() == 2
    url = reverse("revisions_use_files", args=(article.pk, editor_revision.pk, "manuscript"))
    response = client.get(url)
    article.refresh_from_db()
    assert response.status_code == 302
    assert article.manuscript_files.count() == 3
    assert set(article.manuscript_files.all()) == set(editor_revision.manuscript_files.all())
    assert article.supplementary_files.count() == 1
    assert article.data_figure_files.count() == 1
    assert editor_revision.manuscript_files.count() == 3
    assert editor_revision.supplementary_files.count() == 2
    assert editor_revision.data_figure_files.count() == 2


@pytest.mark.django_db
def test_revision_file_replace_no_perms(
    review_settings,
    editor_revision: EditorRevisionRequest,
):
    """Non Corresponding author cannot access revision file view."""
    article = editor_revision.article
    client = Client()
    author = article.author_accounts.exclude(pk=article.correspondence_author.pk).first()
    client.force_login(author)
    assert article.manuscript_files.count() == 1
    assert article.supplementary_files.count() == 1
    assert article.data_figure_files.count() == 1
    assert editor_revision.manuscript_files.count() == 3
    assert editor_revision.supplementary_files.count() == 2
    assert editor_revision.data_figure_files.count() == 2
    url = reverse("revisions_use_files", args=(article.pk, editor_revision.pk, "manuscript"))
    response = client.get(url)
    article.refresh_from_db()
    assert response.status_code == 403
    assert article.manuscript_files.count() == 1
    assert article.supplementary_files.count() == 1
    assert article.data_figure_files.count() == 1
    assert editor_revision.manuscript_files.count() == 3
    assert editor_revision.supplementary_files.count() == 2
    assert editor_revision.data_figure_files.count() == 2


@pytest.mark.django_db
def test_editor_with_kdws_list_retrieval(article_with_keywords, editors_with_keywords):
    """Test the function get_editors_with_keywords().

    Note that editors_with_keywords is a list of 4 editors such that
    - e0: k0 k1 k2
    - e1: k0 k1 k2
    - e2: k0 k1
    - e3:          k3
    and article_with_keywords has k0 k1 k2
    """
    editors_list = Account.objects.get_editors_with_keywords(article_with_keywords, editors_with_keywords[0])

    assert editors_list[0] == editors_with_keywords[1].janeway_account
    assert editors_list[2] == editors_with_keywords[3].janeway_account

    editors_list = Account.objects.get_editors_with_keywords(article_with_keywords, editors_with_keywords[1])

    assert editors_list[0] == editors_with_keywords[0].janeway_account
    assert editors_list[2] == editors_with_keywords[3].janeway_account

    editors_list = Account.objects.get_editors_with_keywords(article_with_keywords)

    assert editors_list[0] == editors_with_keywords[0].janeway_account
    assert editors_list[1] == editors_with_keywords[1].janeway_account
    assert editors_list[2] == editors_with_keywords[2].janeway_account
    assert editors_list[3] == editors_with_keywords[3].janeway_account


@pytest.mark.django_db
def test_editor_with_kdws_scoped_to_article_journal(article_with_keywords, editors_with_keywords, keywords, press):
    """
    Keyword matches from a different journal's StaffWorkloadParameters must not count towards
    num_shared_kwds/max_matching_keyword_weight for an article in a different journal.
    """
    other_journal = _journal_factory("OTHERJ", press)
    cross_journal_editor = editors_with_keywords[0]
    # editors_with_keywords[0] already matches k0/k1/k2 (all weight 0) via StaffKeyword rows in
    # `journal`. Give them an additional, higher-weight match to the same keyword, but in a
    # different journal, and confirm it is not counted for an article in `journal`.
    other_params = StaffWorkloadParameters.objects.create(user=cross_journal_editor, journal=other_journal)
    StaffKeyword.objects.create(parameters=other_params, keyword=keywords[0], weight=99)

    editors_list = Account.objects.get_editors_with_keywords(article_with_keywords)
    matched = editors_list.get(pk=cross_journal_editor.janeway_account.pk)

    assert matched.num_shared_kwds == 3
    assert matched.max_matching_keyword_weight == 0


@pytest.mark.django_db
def test_editor_archived(
    assigned_article: Article,
    section_editor: JCOMProfile,
    submitted_articles: list[Article],
    fake_request: HttpRequest,
):
    """
    Archived articles for an editor are either past review still assigned to them, and article with past assignment.
    """
    fake_request.user = section_editor.janeway_account

    past_assigned = submitted_articles[0]
    accepted = submitted_articles[1]

    past_assigned.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    past_assigned.articleworkflow.save()
    accepted.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    accepted.articleworkflow.save()
    past_assignment = AssignToEditor(
        editor=section_editor.janeway_account,
        article=past_assigned,
        request=fake_request,
        first_assignment=True,
    ).run()
    past_assigned.articleworkflow.refresh_from_db()
    HandleEditorDeclinesAssignment(
        editor=section_editor.janeway_account,
        assignment=past_assignment,
        request=fake_request,
        form_data={"decline_reason": "other"},
    ).run()

    AssignToEditor(
        editor=section_editor.janeway_account,
        article=accepted,
        request=fake_request,
        first_assignment=True,
    ).run()
    accepted.articleworkflow.refresh_from_db()
    HandleDecision(
        workflow=accepted.articleworkflow,
        form_data={
            "decision": ArticleWorkflow.Decisions.ACCEPT,
            "decision_editor_report": "test",
        },
        user=section_editor.janeway_account,
        request=fake_request,
    ).run()
    accepted.articleworkflow.refresh_from_db()

    view = EditorArchived()
    view.request = fake_request
    view.setup(fake_request)
    qs = view.get_queryset()
    assert qs.count() == 2
    assert accepted.articleworkflow in qs
    assert past_assigned.articleworkflow in qs


@pytest.mark.django_db
def test_eo_select_editor(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    eo_user: JCOMProfile,
    review_settings,
):
    """An EO can select an editor for an article without an existing editor."""
    submitted_workflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    submitted_workflow.save()
    fake_request.user = eo_user.janeway_account
    fake_request.method = "POST"
    fake_request.POST = {
        "selected_editor": section_editor.janeway_account.pk,
        "note_for_past_editor": "test",
        "note_for_new_editor": "test",
    }
    StaffWorkloadParameters.objects.create(
        user=section_editor.janeway_account,
        journal=submitted_workflow.article.journal,
        workload=10,
    )
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow
    form = view.get_form()
    assert form.is_valid()
    response = view.form_valid(form)
    assert response.status_code == 302
    submitted_workflow.refresh_from_db()
    assert is_article_editor(submitted_workflow, section_editor.janeway_account)
    assert WjsEditorAssignment.objects.get(article=submitted_workflow.article).editor == section_editor.janeway_account


@pytest.mark.django_db
def test_eo_select_new_editor(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    review_settings,
):
    """An EO can select an editor for an article with an existing editor."""
    normal_user.janeway_account.add_account_role("section-editor", assigned_article.journal)
    StaffWorkloadParameters.objects.create(
        user=normal_user.janeway_account,
        journal=assigned_article.journal,
        workload=10,
    )
    old_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = eo_user.janeway_account
    fake_request.method = "POST"
    fake_request.POST = {
        "selected_editor": normal_user.janeway_account.pk,
        "note_for_past_editor": "test",
        "note_for_new_editor": "test",
    }
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (assigned_article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = assigned_article.articleworkflow
    form = view.get_form()
    assert form.is_valid()
    response = view.form_valid(form)
    assert response.status_code == 302
    assigned_article.articleworkflow.refresh_from_db()
    assert is_article_editor(assigned_article.articleworkflow, normal_user.janeway_account)
    assert not is_article_editor(assigned_article.articleworkflow, old_editor)
    assert (
        WjsEditorAssignment.objects.get(article=assigned_article.articleworkflow.article).editor
        == normal_user.janeway_account
    )
    assert PastEditorAssignment.objects.get(article=assigned_article.articleworkflow.article).editor == old_editor


@pytest.mark.django_db
def test_supervisor_assign_editor_keeps_unavailable_visible_excludes_non_section_editor(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    review_settings,
):
    """
    _editors_with_keywords keeps every SECTION_EDITOR_ROLE editor visible regardless of
    availability (matching wjs-develop's pre-existing behavior — availability differentiates,
    it never excludes) but still restricts the role set to SECTION_EDITOR_ROLE only (D6).
    """
    journal = submitted_workflow.article.journal
    StaffWorkloadParameters.objects.create(user=section_editor.janeway_account, journal=journal, workload=10)

    disabled_editor = create_jcom_user("disabled_section_editor")
    disabled_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(
        user=disabled_editor.janeway_account,
        journal=journal,
        workload=10,
        enabled=False,
    )

    unconfigured_editor = create_jcom_user("unconfigured_section_editor")
    unconfigured_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    # Deliberately no StaffWorkloadParameters row at all for this one.

    plain_editor_role_user = create_jcom_user("plain_editor_role")
    plain_editor_role_user.add_account_role(constants.EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(user=plain_editor_role_user.janeway_account, journal=journal, workload=10)

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = view._editors_with_keywords(search_text=None)

    assert section_editor.janeway_account in candidates
    assert disabled_editor.janeway_account in candidates
    assert unconfigured_editor.janeway_account in candidates
    assert plain_editor_role_user.janeway_account not in candidates


@pytest.mark.django_db
def test_supervisor_assign_editor_annotates_and_orders_by_workload(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    review_settings,
):
    """
    _editors_with_keywords annotates available_workload_month and orders candidates with no
    keyword match (both editors here have none) by descending 30-day workload availability.
    """
    journal = submitted_workflow.article.journal
    StaffWorkloadParameters.objects.create(
        user=section_editor.janeway_account,
        journal=journal,
        workload=1,
    )

    high_availability_editor = create_jcom_user("high_availability_editor")
    high_availability_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(
        user=high_availability_editor.janeway_account,
        journal=journal,
        workload=10,
    )

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = list(view._editors_with_keywords(search_text=None))

    section_editor_candidate = next(c for c in candidates if c.pk == section_editor.janeway_account.pk)
    high_availability_candidate = next(c for c in candidates if c.pk == high_availability_editor.janeway_account.pk)
    assert section_editor_candidate.available_workload_month == 1
    assert high_availability_candidate.available_workload_month == 10
    assert candidates.index(high_availability_candidate) < candidates.index(section_editor_candidate)


@pytest.mark.django_db
def test_supervisor_assign_editor_ranks_keyword_match_ahead_of_no_match(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    keywords,
    review_settings,
):
    """
    _editors_with_keywords must rank a real keyword match ahead of a non-match.

    Regression guard for the manual-view ordering mirroring rank_editor_parameters: the view orders by
    keyword_score nulls-last. Postgres sorts NULLs FIRST under a plain DESC order_by, so a non-matching
    editor (keyword_score is NULL) would otherwise rank ahead of any editor with a real, even low-weight, match.
    """
    journal = submitted_workflow.article.journal
    submitted_workflow.article.keywords.add(keywords[0])

    matched_editor = create_jcom_user("matched_section_editor")
    matched_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    matched_parameters = StaffWorkloadParameters.objects.create(
        user=matched_editor.janeway_account,
        journal=journal,
        workload=10,
        enabled=True,
    )
    StaffKeyword.objects.create(parameters=matched_parameters, keyword=keywords[0], weight=1)

    unmatched_editor = create_jcom_user("unmatched_section_editor")
    unmatched_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(
        user=unmatched_editor.janeway_account,
        journal=journal,
        workload=10,
        enabled=True,
    )

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = list(view._editors_with_keywords(search_text=None))

    matched_candidate = next(c for c in candidates if c.pk == matched_editor.janeway_account.pk)
    unmatched_candidate = next(c for c in candidates if c.pk == unmatched_editor.janeway_account.pk)
    assert matched_candidate.max_matching_keyword_weight == 1
    assert unmatched_candidate.max_matching_keyword_weight is None
    assert candidates.index(matched_candidate) < candidates.index(unmatched_candidate)
    assert matched_candidate.keyword_score is not None, "matched editor has a score"
    assert unmatched_candidate.keyword_score is None, "unmatched editor has no score"


@pytest.mark.django_db
def test_supervisor_assign_editor_ranks_by_keyword_score(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    keywords,
    review_settings,
):
    """The manual list orders by keyword_score: broad weighted coverage ahead of one strong keyword."""
    journal = submitted_workflow.article.journal
    for keyword in (keywords[0], keywords[1], keywords[2]):
        submitted_workflow.article.keywords.add(keyword)

    def _editor(name, weights):
        editor = create_jcom_user(name)
        editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
        parameters = StaffWorkloadParameters.objects.create(
            user=editor.janeway_account, journal=journal, workload=10, enabled=True
        )
        for keyword, weight in weights:
            StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=weight)
        return editor.janeway_account

    narrow = _editor("narrow_section_editor", [(keywords[0], 100)])
    broad = _editor("broad_section_editor", [(keywords[0], 80), (keywords[1], 80), (keywords[2], 80)])

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = list(view._editors_with_keywords(search_text=None))

    assert candidates.index(broad) < candidates.index(narrow), "keyword_score orders the manual list"


@pytest.mark.django_db
def test_supervisor_assign_editor_breaks_score_ties_by_shared_keyword_count(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    keywords,
    review_settings,
):
    """With an equal keyword_score (all weights 0), the manual list ranks more shared keywords first."""
    journal = submitted_workflow.article.journal
    for keyword in (keywords[0], keywords[1]):
        submitted_workflow.article.keywords.add(keyword)

    def _editor(name, workload, kwds):
        editor = create_jcom_user(name)
        editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
        parameters = StaffWorkloadParameters.objects.create(
            user=editor.janeway_account, journal=journal, workload=workload, enabled=True
        )
        for keyword in kwds:
            StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=0)
        return editor.janeway_account

    # The one-keyword editor has the higher workload capacity, so availability alone would rank it first.
    one_kwd = _editor("one_kwd_section_editor", 20, [keywords[0]])
    two_kwds = _editor("two_kwds_section_editor", 5, [keywords[0], keywords[1]])

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = list(view._editors_with_keywords(search_text=None))

    scores = {c.pk: c.keyword_score for c in candidates}
    assert scores[one_kwd.pk] == scores[two_kwds.pk], "precondition: equal keyword_score"
    assert candidates.index(two_kwds) < candidates.index(one_kwd), "shared keyword count breaks the score tie"


@pytest.mark.django_db
def test_select_new_editor_page_shows_keyword_score(
    submitted_workflow: ArticleWorkflow,
    eo_user: JCOMProfile,
    create_jcom_user: Callable,
    keywords,
    review_settings,
):
    """The manual editor table shows each editor's keyword score as a percentage, or a dash."""
    journal = submitted_workflow.article.journal
    submitted_workflow.article.keywords.add(keywords[0], keywords[1])
    for name, weights in (("scored_editor", [(keywords[0], 100)]), ("unscored_editor", [])):
        editor = create_jcom_user(name)
        editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
        parameters = StaffWorkloadParameters.objects.create(
            user=editor.janeway_account, journal=journal, workload=10, enabled=True
        )
        for keyword, weight in weights:
            StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=weight)
    client = Client()
    client.force_login(eo_user.janeway_account)

    with override_settings(WJS_SHOW_EDITOR_KEYWORDS=[journal.code]):
        response = client.get(reverse("wjs_assign_editor", args=(submitted_workflow.pk,)))

    content = response.content.decode()
    assert response.status_code == 200, "EO can open the page"
    assert "Keyword score" in content, "column header shown"
    assert "50%" in content, "one of two equally weighted keywords at full weight = 50%"
    assert "—" in content, "editors without a shared keyword show a dash"


@pytest.mark.django_db
def test_article_redirect(
    assigned_article: Article,
    fake_request: HttpRequest,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    review_settings,
):
    view = ArticleIdToDetails()
    view.request = fake_request
    view.setup(fake_request)
    view.args = ()
    view.kwargs = {"article_id": assigned_article.pk}
    url = view.get_redirect_url(**view.kwargs)
    assert url == reverse("wjs_article_details", kwargs={"pk": assigned_article.articleworkflow.pk})

    view.kwargs = {"article_id": assigned_article.articleworkflow.pk}
    url = view.get_redirect_url(**view.kwargs)
    assert url == reverse("wjs_article_details", kwargs={"pk": assigned_article.articleworkflow.pk})

    view.kwargs = {"article_id": assigned_article.articleworkflow.pk + 1}
    from django.http import Http404

    with pytest.raises(Http404):
        url = view.get_redirect_url(**view.kwargs)


@pytest.mark.django_db
def test_assigns_themselves_as_reviewer_no_double_assignation(
    assigned_article: Article,
    client: Client,
    review_form: ReviewForm,
):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)

    url = (
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"editor_assigns_themselves_as_reviewer/{assigned_article.articleworkflow.pk}/"
    )

    response = client.get(url)
    assert response.status_code == 200

    response = client.post(url, data={"acceptance_due_date": localtime(now()).date() + datetime.timedelta(days=7)})
    assert response.status_code == 200

    assert (
        review_models.ReviewAssignment.objects.filter(
            reviewer=assignment.editor,
            article=assigned_article,
        ).count()
        == 1
    )

    response = client.post(url, data={"acceptance_due_date": localtime(now()).date() + datetime.timedelta(days=7)})
    assert response.status_code == 200

    assert (
        review_models.ReviewAssignment.objects.filter(
            reviewer=assignment.editor,
            article=assigned_article,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_editor_selects_reviewer_no_double_assignment(
    assigned_article: Article,
    client: Client,
    review_form: ReviewForm,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
):
    reviewer_1 = create_jcom_user("reviewer_1")
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)

    url = (
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"select_reviewer/{assigned_article.articleworkflow.pk}/"
    )

    response = client.get(url)
    assert response.status_code == 200

    response = client.post(
        url,
        data={
            "reviewer": reviewer_1.pk,
            "message": "I invite you to review this article.",
        },
    )
    assert response.status_code == 200

    assert (
        review_models.ReviewAssignment.objects.filter(
            reviewer=reviewer_1,
            article=assigned_article,
        ).count()
        == 1
    )

    response = client.post(
        url,
        data={
            "reviewer": reviewer_1.pk,
            "message": "I invite you to review this article.",
        },
    )
    assert response.status_code == 200

    assert (
        review_models.ReviewAssignment.objects.filter(
            reviewer=reviewer_1,
            article=assigned_article,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_last_revision_date(
    admin,
    fake_request: HttpRequest,
    submitted_articles,
    install_wjs_submission_settings,
):
    """
    Calculated last_revision_date matches the templatetag selection.
    """
    ArticleWorkflow.objects.update(eo_in_charge=admin, state=ArticleWorkflow.ReviewStates.EDITOR_SELECTED)
    articles = Article.objects.all()
    article_with_multiple_revisions = articles[0]
    article_with_completed_revision = articles[1]
    article_with_open_revision = articles[2]
    article_without_revisions = articles[3]

    rr_multiple_revisions_1 = ReviewRound.objects.create(round_number=1, article=article_with_multiple_revisions)
    EditorRevisionRequest.objects.create(
        article=article_with_multiple_revisions,
        editor=admin,
        review_round=rr_multiple_revisions_1,
        editor_decision=EditorDecision.objects.create(
            review_round=rr_multiple_revisions_1,
            editor=admin,
            workflow=article_with_multiple_revisions.articleworkflow,
            decision=ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
        date_due=now(),
        date_completed=now() + datetime.timedelta(days=10),
    )
    rr_multiple_revisions_2 = ReviewRound.objects.create(round_number=2, article=article_with_multiple_revisions)
    EditorRevisionRequest.objects.create(
        article=article_with_multiple_revisions,
        editor=admin,
        review_round=rr_multiple_revisions_2,
        editor_decision=EditorDecision.objects.create(
            review_round=rr_multiple_revisions_2,
            editor=admin,
            workflow=article_with_multiple_revisions.articleworkflow,
            decision=ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
        date_due=now(),
        date_completed=now() + datetime.timedelta(days=10),
    )
    rr_multiple_revisions_3 = ReviewRound.objects.create(round_number=3, article=article_with_multiple_revisions)
    EditorRevisionRequest.objects.create(
        article=article_with_multiple_revisions,
        editor=admin,
        review_round=rr_multiple_revisions_3,
        editor_decision=EditorDecision.objects.create(
            review_round=rr_multiple_revisions_3,
            editor=admin,
            workflow=article_with_multiple_revisions.articleworkflow,
            decision=ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
        date_due=now(),
    )

    rr_completed_revision = ReviewRound.objects.create(round_number=1, article=article_with_completed_revision)
    EditorRevisionRequest.objects.create(
        article=article_with_completed_revision,
        editor=admin,
        review_round=rr_completed_revision,
        editor_decision=EditorDecision.objects.create(
            review_round=rr_completed_revision,
            editor=admin,
            workflow=article_with_completed_revision.articleworkflow,
            decision=ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
        date_due=now(),
        date_completed=now() + datetime.timedelta(days=10),
    )
    rr_open_revision = ReviewRound.objects.create(round_number=1, article=article_with_open_revision)
    EditorRevisionRequest.objects.create(
        article=article_with_open_revision,
        editor=admin,
        review_round=rr_open_revision,
        editor_decision=EditorDecision.objects.create(
            review_round=rr_open_revision,
            editor=admin,
            workflow=article_with_open_revision.articleworkflow,
            decision=ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
        date_due=now(),
    )
    fake_request.GET = QueryDict()
    fake_request.user = admin
    view_obj = EOPending()
    view_obj.kwargs = {}
    view_obj.args = {}
    view_obj.request = fake_request
    view_obj.load_initial(fake_request)
    qs = view_obj.get_queryset()
    assert qs.exists()
    assert article_with_completed_revision.articleworkflow in qs
    assert article_with_open_revision.articleworkflow in qs
    assert article_with_multiple_revisions.articleworkflow in qs
    article_with_multiple_revisions_from_qs = qs.get(pk=article_with_multiple_revisions.articleworkflow.pk)
    article_with_completed_revision_from_qs = qs.get(pk=article_with_completed_revision.articleworkflow.pk)
    article_with_open_revision_from_qs = qs.get(pk=article_with_open_revision.articleworkflow.pk)
    article_without_revisions_from_qs = qs.get(pk=article_without_revisions.articleworkflow.pk)
    assert article_with_multiple_revisions_from_qs.last_revision_date == get_version_submission_date(
        article_with_multiple_revisions
    )
    assert article_with_completed_revision_from_qs.last_revision_date == get_version_submission_date(
        article_with_completed_revision
    )
    assert article_with_open_revision_from_qs.last_revision_date == get_version_submission_date(
        article_with_open_revision
    )
    assert (
        article_without_revisions_from_qs.last_revision_date
        == article_without_revisions_from_qs.article.date_submitted
    )


@pytest.mark.django_db
def test_article_details_fix_site_url(
    press: Press,
    journal: Journal,
    article_factory: Callable,
    fake_request: HttpRequest,
    normal_user: JCOMProfile,
):
    wrong_journal = _journal_factory("JCOMAL", press, domain="jcomal.sissa.it")
    article = article_factory(
        journal=journal,
        correspondence_author=normal_user,
        title="",
    )
    assert article.journal.site_url() == journal.site_url() == "http://testserver/JCOM"
    assert wrong_journal.site_url() == "http://testserver/JCOMAL"

    view = ArticleDetails()
    fake_request.user = normal_user
    fake_request.journal = wrong_journal
    view.request = fake_request
    view.setup(fake_request, pk=article.articleworkflow.pk)

    response = view.get(fake_request, pk=article.articleworkflow.pk)
    expected_path = reverse("wjs_article_details", kwargs={"pk": article.articleworkflow.pk})
    assert isinstance(response, HttpResponseRedirect)
    assert article.journal.site_url(expected_path) == response.url


@pytest.mark.django_db
def test_article_details_no_redirect(
    journal: Journal,
    article_factory: Callable,
    fake_request: HttpRequest,
    normal_user: JCOMProfile,
):

    article = article_factory(
        journal=journal,
        correspondence_author=normal_user,
        title="",
    )
    assert article.journal.site_url() == journal.site_url() == "http://testserver/JCOM"

    view = ArticleDetails()
    fake_request.user = normal_user
    fake_request.journal = journal
    view.request = fake_request
    view.setup(fake_request, pk=article.articleworkflow.pk)

    response = view.get(fake_request, pk=article.articleworkflow.pk)
    assert not isinstance(response, HttpResponseRedirect)
    assert response.status_code == 200


@pytest.mark.django_db
def test_article_decision_triggered_twice_is_serialized(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
):
    """HandleDecision serializes concurrent decisions via a row lock instead of storing a duplicate.

    When two requests reach ArticleDecision before the first one commits (e.g. a double click,
    or two concurrent/manual requests), both load the workflow while it is still in
    EDITOR_SELECTED (the state ArticleDecision.get_queryset() filters on) and both build a valid
    DecisionForm. The first form.save() runs HandleDecision and stores the decision, moving the
    article out of EDITOR_SELECTED. The second form still holds a stale workflow (EDITOR_SELECTED
    in memory), so it is valid; but HandleDecision.run() locks the workflow row with
    select_for_update() and re-reads the current state, so check_conditions() now fails and a
    ValidationError is raised instead of a duplicate EditorDecision violating the unique
    constraint on (workflow, review_round, decision).
    """
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor

    workflow_pk = assigned_article.articleworkflow.pk
    # Two requests arriving in rapid succession both load the workflow while it is still
    # EDITOR_SELECTED, before either decision is committed.
    workflow_first = ArticleWorkflow.objects.get(pk=workflow_pk)
    workflow_second = ArticleWorkflow.objects.get(pk=workflow_pk)

    def build_decision_form(workflow: ArticleWorkflow) -> DecisionForm:
        # Build the form the same way ArticleDecision.get_form_kwargs() does.
        return DecisionForm(
            data={
                "decision": ArticleWorkflow.Decisions.ACCEPT,
                "decision_editor_report": "Accepting the paper",
            },
            instance=workflow,
            user=editor,
            request=fake_request,
            initial={"decision": ArticleWorkflow.Decisions.ACCEPT},
            has_pending_reviews=False,
        )

    # First request: the decision is stored and the article leaves EDITOR_SELECTED.
    form_first = build_decision_form(workflow_first)
    assert form_first.is_valid(), form_first.errors
    form_first.save()
    workflow_first.refresh_from_db()
    # Remember the automatic jump from ACCEPTED to READY_FOR_TYPESETTER
    assert workflow_first.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert EditorDecision.objects.filter(workflow=assigned_article.articleworkflow).count() == 1

    # Second request: the form is still valid because its workflow is stale (EDITOR_SELECTED
    # in memory), so the form-level guards don't catch it.
    form_second = build_decision_form(workflow_second)
    assert form_second.is_valid(), form_second.errors

    # But HandleDecision.run() locks the row and re-reads the fresh state, so check_conditions()
    # fails: a ValidationError is raised and no duplicate EditorDecision is stored.
    with pytest.raises(ValidationError):
        form_second.save()
    assert EditorDecision.objects.filter(workflow=assigned_article.articleworkflow).count() == 1


@pytest.mark.django_db
def test_decision_form_decisioneditorreport(
    fake_request: HttpRequest,
    assigned_article: submission_models.Article,
):
    """ """
    editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
    fake_request.user = editor
    workflow = assigned_article.articleworkflow

    # note that white space is trimmed, so we start from the first line
    # and end without a newline:
    text_with_pesky_chars = r"""gt: $x>1$
    lt: $y<1$
    amp: \& co."""
    form = DecisionForm(
        data={
            "decision": ArticleWorkflow.Decisions.ACCEPT,
            "decision_editor_report": text_with_pesky_chars,
        },
        instance=workflow,
        user=editor,
        request=fake_request,
        initial={"decision": ArticleWorkflow.Decisions.ACCEPT},
        has_pending_reviews=False,
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["decision_editor_report"] == text_with_pesky_chars


DECISION_INTRO_WITHOUT_REPORTS = "Please write your report to motivate your decision."
DECISION_INTRO_WITH_REPORTS = "The following form includes any reports that you have just decided to send the authors."
DECISION_LATEX_HINT = "The report can be written in LaTeX."


@pytest.mark.parametrize(
    "reviewer_report_type,latex_hint_expected",
    (
        ("text", False),
        ("tex", True),
        ("tex+text", True),
    ),
)
@pytest.mark.django_db
def test_decision_page_without_reports_intro(
    client: Client,
    assigned_article: Article,
    reviewer_report_type: str,
    latex_hint_expected: bool,
):
    """Without referee reports the decision page asks to motivate the decision, mentioning LaTeX if allowed."""
    setting_handler.save_setting(
        setting_group_name="wjs_review",
        setting_name="reviewer_report_type",
        journal=assigned_article.journal,
        value=reviewer_report_type,
    )
    client.force_login(WjsEditorAssignment.objects.get_current(assigned_article).editor)
    url = reverse("wjs_article_decision", args=(assigned_article.articleworkflow.pk,))

    content = client.get(url, {"decision": ArticleWorkflow.Decisions.ACCEPT}).content.decode()

    assert DECISION_INTRO_WITHOUT_REPORTS in content, "The intro for a decision without reports is shown"
    assert DECISION_INTRO_WITH_REPORTS not in content, "The intro for a decision with reports is not shown"
    assert (DECISION_LATEX_HINT in content) is latex_hint_expected, "LaTeX is mentioned only if the journal allows it"
    expected_intro = (
        f"{DECISION_INTRO_WITHOUT_REPORTS} {DECISION_LATEX_HINT}"
        if latex_hint_expected
        else DECISION_INTRO_WITHOUT_REPORTS
    )
    assert f'<p class="mb-1">{expected_intro}</p>' in content, "The intro is its own block above the field"


@pytest.mark.django_db
def test_decision_page_with_reports_intro_is_unchanged(
    client: Client,
    submitted_review_assignment: WorkflowReviewAssignment,
):
    """With a submitted referee report the decision page keeps its original intro."""
    article = submitted_review_assignment.article
    setting_handler.save_setting(
        setting_group_name="wjs_review",
        setting_name="reviewer_report_type",
        journal=article.journal,
        value="tex",
    )
    client.force_login(WjsEditorAssignment.objects.get_current(article).editor)
    url = reverse("wjs_article_decision", args=(article.articleworkflow.pk,))

    content = client.get(url, {"decision": ArticleWorkflow.Decisions.ACCEPT}).content.decode()

    assert DECISION_INTRO_WITH_REPORTS in content, "The original intro is shown when there are reports"
    assert DECISION_INTRO_WITHOUT_REPORTS not in content, "The no-reports intro is not shown"
    assert DECISION_LATEX_HINT not in content, "The LaTeX hint belongs to the no-reports intro only"
    assert (
        '<p class="mb-1">The following form includes any reports' in content
    ), "The intro is its own block above the field"


@pytest.mark.django_db
def test_authors_accordion_shows_author_records(assigned_article: Article):
    """
    The "Authors" accordion of the status page shows the author records of the paper.

    Author records are not necessarily linked to an account (the sync TeX/DB page can create such
    records), so the accordion must not iterate on article.author_accounts.
    """
    owner = assigned_article.owner
    owner.twitter = "@alice.bsky.social"
    owner.save()
    owner.snapshot_as_author(assigned_article)
    unlinked_author = submission_models.FrozenAuthor.objects.create(
        article=assigned_article,
        author=None,
        order=99,
        first_name="Nobody",
        last_name="Knows",
        frozen_email="nobody@example.com",
        frozen_biography="A biography from the TeX source.",
    )

    rendered = render_to_string(
        "wjs_review/details/elements/metadata_authors.html",
        {
            "has_full_access": True,
            "article": assigned_article,
            "journal": assigned_article.journal,
            "workflow": assigned_article.articleworkflow,
        },
    )
    assert "Nobody Knows" in rendered, "The author record with no account is displayed"
    assert f"modal-{unlinked_author.pk}" in rendered, "...with its own contacts modal"
    assert "nobody@example.com" in rendered, "...showing the email of the author record"
    assert "A biography from the TeX source." in rendered, "...and its biography"
    assert owner.full_name() in rendered, "The author record of the owner is displayed too"
    assert (
        "https://bsky.app/profile/alice.bsky.social" in rendered
    ), "The socials handle of an account is displayed as a bluesky URL"


@pytest.mark.django_db
def test_get_editor_keywords_ordered_by_weight_then_word(journal, editors, keywords):
    """The journal editors page lists an editor's keywords highest weight first, then alphabetically."""
    editor = editors[0]
    parameters = StaffWorkloadParameters.objects.get(user=editor, journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords.get(word="3-keyword"), weight=10)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords.get(word="1-keyword"), weight=50)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords.get(word="2-keyword"), weight=10)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords.get(word="0-keyword"), weight=10)

    words = [staff_keyword.keyword.word for staff_keyword in get_editor_keywords(editor, journal)]

    assert words == ["1-keyword", "0-keyword", "2-keyword", "3-keyword"], f"Unexpected keyword order: {words}"
