"""A director who is an author of a paper must not see it on director lists nor its editorial pages (#3013)."""

import datetime
from typing import Callable, Optional, Type

import pytest
from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest, QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.views.generic.list import MultipleObjectMixin
from journal.models import Issue, IssueType
from submission.models import Article

from wjs.jcom_profile.constants import DIRECTOR_ROLE
from wjs.jcom_profile.models import JCOMProfile

from .. import views
from ..logic import (
    states_when_article_is_considered_archived,
    states_when_article_is_considered_in_production,
    states_when_article_is_considered_in_review_for_eo_and_director,
)
from ..models import (
    ArticleWorkflow,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

DIRECTOR_LISTS = (
    (views.DirectorPending, states_when_article_is_considered_in_review_for_eo_and_director[0]),
    (views.DirectorArchived, states_when_article_is_considered_archived[0]),
    (views.DirectorProduction, states_when_article_is_considered_in_production[0]),
    (views.DirectorWorkOnAPaper, states_when_article_is_considered_in_review_for_eo_and_director[0]),
)


def _make_author(article: Article, user: JCOMProfile, author_kind: str):
    """Make user an author of article, either as frozen author or as correspondence author only."""
    if author_kind == "frozen_author":
        article.authors.add(user.janeway_account)
    else:
        article.correspondence_author = user.janeway_account
        article.save()
        assert not article.frozenauthor_set.filter(author=user.janeway_account).exists()


def _director_list(
    view_class: Type[MultipleObjectMixin], user: JCOMProfile, fake_request: HttpRequest, query: str = ""
):
    """Return the queryset a director sees on the given list view."""
    # GET must be a QueryDict, otherwise it's interpreted by filterset and used as filter parameters
    fake_request.GET = QueryDict(query)
    fake_request.user = user
    view_obj = view_class()
    view_obj.kwargs = {}
    view_obj.args = {}
    view_obj.request = fake_request
    view_obj.load_initial(fake_request)
    return view_obj.get_queryset()


@pytest.fixture
def other_director(create_jcom_user: Callable[[Optional[str]], JCOMProfile], journal) -> JCOMProfile:
    """A second director of the journal, who is not an author of the paper."""
    other = create_jcom_user("Other Director")
    other.add_account_role(DIRECTOR_ROLE, journal)
    return other


@pytest.mark.parametrize("author_kind", ("frozen_author", "correspondence_author_only"))
@pytest.mark.parametrize("view_class,state", DIRECTOR_LISTS)
@pytest.mark.django_db
def test_director_author_does_not_see_own_paper(
    assigned_article: Article,
    director: JCOMProfile,
    other_director: JCOMProfile,
    fake_request: HttpRequest,
    view_class: Type[MultipleObjectMixin],
    state: str,
    author_kind: str,
):
    """A director who is an author (frozen or correspondence) does not find the paper; another director does."""
    workflow = assigned_article.articleworkflow
    workflow.state = state
    workflow.save()
    _make_author(assigned_article, director, author_kind)

    assert not _director_list(view_class, director, fake_request).filter(pk=workflow.pk).exists()
    assert _director_list(view_class, other_director, fake_request).filter(pk=workflow.pk).exists()


@pytest.mark.parametrize("author_kind", ("frozen_author", "correspondence_author_only"))
@pytest.mark.django_db
def test_search_by_title_does_not_return_own_paper(
    assigned_article: Article,
    director: JCOMProfile,
    other_director: JCOMProfile,
    fake_request: HttpRequest,
    author_kind: str,
):
    """ "Search preprints" by title does not return the paper to a director who is an author of it."""
    workflow = assigned_article.articleworkflow
    _make_author(assigned_article, director, author_kind)
    query = f"abstract_or_title={assigned_article.title}"

    own = _director_list(views.DirectorWorkOnAPaper, director, fake_request, query)
    assert not own.filter(pk=workflow.pk).exists()
    others = _director_list(views.DirectorWorkOnAPaper, other_director, fake_request, query)
    assert others.filter(pk=workflow.pk).exists()


@pytest.fixture
def review_assignment(assigned_article_with_reviewer: Article) -> WorkflowReviewAssignment:
    """The review assignment (with its reminders) created by the assigned_article_with_reviewer fixture."""
    return WorkflowReviewAssignment.objects.get(article=assigned_article_with_reviewer)


def _assignment_reminder(assignment: WorkflowReviewAssignment) -> Reminder:
    return Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(WorkflowReviewAssignment),
        object_id=assignment.pk,
    ).first()


@pytest.mark.parametrize("author_kind", ("frozen_author", "correspondence_author_only"))
@pytest.mark.django_db
def test_reminders_pages_forbidden_to_director_author(
    review_assignment: WorkflowReviewAssignment,
    director: JCOMProfile,
    other_director: JCOMProfile,
    author_kind: str,
):
    """Director-authors get 403 on the reminders pages and on the reminder toggle; other directors don't."""
    article = review_assignment.article
    reminder = _assignment_reminder(review_assignment)
    urls = (
        reverse("wjs_article_reminders", kwargs={"pk": article.articleworkflow.pk}),
        reverse("wjs_reminders_per_assignment", kwargs={"pk": review_assignment.pk}),
        reverse("wjs_toggle_reminder", kwargs={"reminder_id": reminder.pk}),
    )
    _make_author(article, director, author_kind)

    client = Client()
    for user, expected in ((director, 403), (other_director, 200)):
        client.force_login(user)
        for url in urls:
            assert client.get(url).status_code == expected, f"{user} on {url}"


@pytest.mark.django_db
def test_assignment_editor_still_sees_assignment_reminders(
    review_assignment: WorkflowReviewAssignment,
    section_editor: JCOMProfile,
):
    """The article editor keeps access to the per-assignment reminders page."""
    client = Client()
    client.force_login(section_editor)

    url = reverse("wjs_reminders_per_assignment", kwargs={"pk": review_assignment.pk})

    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_toggle_reminder_without_article_keeps_journal_level_access(
    review_assignment: WorkflowReviewAssignment,
    director: JCOMProfile,
):
    """A reminder whose target has no article can't be checked by article: directors keep journal-level access."""
    reminder = _assignment_reminder(review_assignment)
    reminder.pk = None
    reminder.content_type = ContentType.objects.get_for_model(Account)
    reminder.object_id = director.pk
    reminder.save()
    assert reminder.get_related_article() is None
    client = Client()
    client.force_login(director)

    response = client.get(reverse("wjs_toggle_reminder", kwargs={"reminder_id": reminder.pk}))

    assert response.status_code == 200


@pytest.fixture
def pending_issue(assigned_article: Article, journal) -> Issue:
    """A pending (future) issue containing the assigned article."""
    issue = Issue.objects.create(
        journal=journal,
        date=timezone.localtime(timezone.now()).date() + datetime.timedelta(days=30),
        issue_type=IssueType.objects.filter(journal=journal).first(),
    )
    issue.articles.add(assigned_article)
    return issue


@pytest.mark.parametrize("author_kind", ("frozen_author", "correspondence_author_only"))
@pytest.mark.django_db
def test_pending_issues_hide_editor_of_director_authored_paper(
    pending_issue: Issue,
    assigned_article: Article,
    section_editor: JCOMProfile,
    director: JCOMProfile,
    other_director: JCOMProfile,
    eo_user: JCOMProfile,
    author_kind: str,
):
    """The "Pending Issues" list does not show a director the editor of a paper they are an author of."""
    editor_name = str(section_editor.janeway_account)
    url = reverse("wjs_review_director_issues_list")
    _make_author(assigned_article, director, author_kind)
    client = Client()

    client.force_login(director)
    response = client.get(url)
    assert response.status_code == 200
    assert str(assigned_article.pk) in response.content.decode(), "the paper is still listed"
    assert editor_name not in response.content.decode(), "but its editor is hidden"

    client.force_login(other_director)
    assert editor_name in client.get(url).content.decode(), "non-author directors see the editor"


@pytest.mark.django_db
def test_eo_issue_list_shows_editor_even_to_eo_who_is_author(
    pending_issue: Issue,
    assigned_article: Article,
    section_editor: JCOMProfile,
    eo_user: JCOMProfile,
):
    """EO is not affected by authorship: the EO issue list keeps showing the editor."""
    assigned_article.authors.add(eo_user.janeway_account)
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs_review_eo_issues_list"))

    assert str(section_editor.janeway_account) in response.content.decode()


@pytest.mark.parametrize("author_kind", ("frozen_author", "correspondence_author_only"))
@pytest.mark.django_db
def test_search_by_id_does_not_return_own_paper(
    assigned_article: Article,
    director: JCOMProfile,
    other_director: JCOMProfile,
    fake_request: HttpRequest,
    author_kind: str,
):
    """ "Search preprints" by article id or preprint id does not return the paper to a director-author."""
    workflow = assigned_article.articleworkflow
    _make_author(assigned_article, director, author_kind)
    for value in (assigned_article.pk, workflow.preprint_id):
        query = f"article_identifiers={value}"
        own = _director_list(views.DirectorWorkOnAPaper, director, fake_request, query)
        assert not own.filter(pk=workflow.pk).exists(), value
        others = _director_list(views.DirectorWorkOnAPaper, other_director, fake_request, query)
        assert others.filter(pk=workflow.pk).exists(), value


@pytest.mark.django_db
def test_toggle_reminder_with_article_without_workflow_keeps_journal_level_access(
    assigned_article: Article,
    director: JCOMProfile,
):
    """A reminder on an article that has no workflow (legacy data) must not crash: journal-level access applies."""
    reminder = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(WjsEditorAssignment),
    ).first()
    assert reminder.get_related_article() == assigned_article
    ArticleWorkflow.objects.filter(article=assigned_article).delete()
    client = Client()
    client.force_login(director)

    response = client.get(reverse("wjs_toggle_reminder", kwargs={"reminder_id": reminder.pk}))

    assert response.status_code == 200
