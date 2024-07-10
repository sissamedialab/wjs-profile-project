import datetime
from typing import Iterable, List

import pytest
from core.models import Account
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.http import HttpRequest
from django.test.client import Client
from django.urls import reverse
from django.utils.timezone import now
from review.models import ReviewAssignment, ReviewForm
from submission import models as submission_models
from submission.models import Article
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token, render_template_from_setting

from ..logic import AssignToEditor, HandleDecision, HandleEditorDeclinesAssignment
from ..models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Message,
    WjsEditorAssignment,
)
from ..templatetags.wjs_articles import user_is_coauthor
from ..views import EditorArchived, SelectReviewer


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
    """An editor cannot set the acceptance_due_date in the past wrt now().date()."""
    url = reverse("wjs_editor_assigns_themselves_as_reviewer", args=(assigned_article.pk,))
    post_data = {
        "acceptance_due_date": now().date() - datetime.timedelta(days=1),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    assert dict(response.context["form"].errors) == {"acceptance_due_date": ["Date must be in the future"]}


@pytest.mark.django_db
def test_editor_assigns_themselves_as_reviewer_acceptance_due_date_too_much_in_the_future(
    client: Client,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """An editor cannot set the acceptance_due_date after settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX wrt now().date()."""
    url = reverse("wjs_editor_assigns_themselves_as_reviewer", args=(assigned_article.pk,))
    today = now().date()
    date_min = today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MIN)
    date_max = today + datetime.timedelta(days=settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX)
    post_data = {
        "acceptance_due_date": date_max + datetime.timedelta(days=1),
    }
    client.force_login(section_editor.janeway_account)
    response = client.post(url, post_data)
    assert response.status_code == 200
    errors = {"acceptance_due_date": [f"Date must be between {date_min} and {date_max}"]}
    assert dict(response.context["form"].errors) == errors


@pytest.mark.django_db
def test_select_reviewer_queryset_for_editor(
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    assigned_article: submission_models.Article,
    submitted_articles: Iterable[submission_models.Article],
):
    """An editor can only access SelectReviewer for their own articles."""
    fake_request.user = section_editor.janeway_account

    view = SelectReviewer()
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

    view = SelectReviewer()
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


@pytest.mark.django_db
def test_select_reviewer_acceptance_due_date_in_the_past(
    client: Client,
    section_editor: JCOMProfile,
    reviewer: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """An editor cannot set the acceptance_due_date in the past wrt now().date()."""
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    post_data = {
        "reviewer": reviewer.pk,
        "message": "test_select_reviewer_acceptance_due_date_in_the_past() test message",
        "acceptance_due_date": now().date() - datetime.timedelta(days=1),
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
    """An editor cannot set the acceptance_due_date after settings.DEFAULT_ACCEPTANCE_DUE_DATE_MAX wrt now().date()."""
    url = reverse("wjs_select_reviewer", args=(assigned_article.pk,))
    today = now().date()
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
    errors = {"acceptance_due_date": [f"Date must be between {date_min} and {date_max}"]}
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
    }
    response = client.post(url, data=data)
    assert response.status_code == 302

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
        if field != "message":
            assert getattr(invited_user, field) == data[field]
    assert invited_user.invitation_token == invitation_token

    review_assignment_subject = render_template_from_setting(
        setting_group_name="email_subject",
        setting_name="subject_review_assignment",
        journal=assigned_article.journal,
        request=fake_request,
        context={"article": assigned_article},
        template_is_setting=True,
    )
    acceptance_url = f"{gdpr_acceptance_url}?access_code={assigned_article.reviewassignment_set.first().access_code}"
    assert len(mail.outbox) == 1
    # Check messages
    assert Message.objects.count() == 1
    message_to_reviewer = Message.objects.first()
    assert review_assignment_subject == message_to_reviewer.subject
    assert assigned_article.title in message_to_reviewer.subject
    assert "random message" in message_to_reviewer.body
    assert acceptance_url in message_to_reviewer.body
    assert message_to_reviewer.message_type == "Verbose"
    assert message_to_reviewer.actor == section_editor
    assert list(message_to_reviewer.recipients.all()) == [invited_user.janeway_account]


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
        assert Message.objects.count() == 2
        # Message related to the reviewer accepting the assignment
        message = Message.objects.last()
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [review_assignment_invited_user.editor]
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_accept_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_accept_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context={
                "article": review_assignment_invited_user.article,
                "request": request,
                "review_assignment": review_assignment_invited_user,
                "review_url": reverse(
                    "wjs_review_review", kwargs={"assignment_id": review_assignment_invited_user.id}
                ),
            },
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
    else:
        # No new message created
        assert Message.objects.count() == 1
        assert "You must accept GDPR to continue" in response.content.decode()
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
        assert Message.objects.count() == 2
        # Message related to the reviewer accepting the assignment
        message = Message.objects.last()
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [review_assignment_invited_user.editor]
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_accept_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_accept_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context={
                "article": review_assignment_invited_user.article,
                "request": request,
                "review_assignment": review_assignment_invited_user,
                "review_url": reverse(
                    "wjs_review_review", kwargs={"assignment_id": review_assignment_invited_user.id}
                ),
            },
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
        assert "You must accept GDPR to continue" in response.content.decode()
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
        assert "You must accept GDPR to continue" not in response.content.decode()
    else:
        assert "You must accept GDPR to continue" in response.content.decode()
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
        "decline_reason": reason,
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
        assert Message.objects.count() == 2
        # Message related to the reviewer declining the assignment
        message = Message.objects.last()
        assert message.actor == invited_user
        assert list(message.recipients.all()) == [review_assignment_invited_user.editor]
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_decline_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_decline_acknowledgement",
            journal=review_assignment_invited_user.article.journal,
            request=request,
            context={
                "article": review_assignment_invited_user.article,
                "request": request,
                "review_assignment": review_assignment_invited_user,
                "review_url": reverse(
                    "wjs_review_review", kwargs={"assignment_id": review_assignment_invited_user.id}
                ),
            },
            template_is_setting=True,
        )
        assert message.subject == message_subject
        assert message.body == message_body
    else:
        # No new message created
        assert Message.objects.count() == 1


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
):
    client = Client()
    client.force_login(admin)
    url = reverse("submit_review", args=(article.pk,))
    coauthors_email = list(
        article.authors.exclude(email=article.correspondence_author.email).values_list("email", flat=True),
    )

    response = client.post(url, data={"next_step": "next_step"})
    assert response.status_code == 302
    assert len(mail.outbox) == article.authors.count()

    for m in mail.outbox:
        if m.subject == f"[{article.journal.code}] Coauthor - Article Submission":
            assert m.to == coauthors_email
        else:
            assert m.to == [article.correspondence_author.email]


@pytest.mark.django_db
def test_revision_file_replace(
    review_settings,
    article: Article,
    editor_revision: EditorRevisionRequest,
):
    """Files from editorrevision are used to replace article files."""
    client = Client()
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
    article: Article,
    editor_revision: EditorRevisionRequest,
):
    """Non Corresponding author cannot access view."""
    client = Client()
    client.force_login(article.authors.exclude(pk=article.correspondence_author.pk).first())
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
            "decision_internal_note": "test",
        },
        user=section_editor.janeway_account,
        request=fake_request,
    ).run()
    accepted.articleworkflow.refresh_from_db()

    view = EditorArchived()
    view.request = fake_request
    view.setup(fake_request)
    qs = view.get_queryset()
    assert qs.count() == 1
    assert accepted.articleworkflow not in qs
    assert past_assigned.articleworkflow in qs
