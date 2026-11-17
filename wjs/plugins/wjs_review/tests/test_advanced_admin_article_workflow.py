"""Tests for the Article workflows page of the advanced admin."""

import pytest
from django.test import RequestFactory
from django.urls import reverse
from plugins.wjs_review.advanced_admin.admin import (
    ArticleWorkflowAdmin,
    ArticleWorkflowProxy,
)
from plugins.wjs_review.models import ArticleWorkflow
from submission.models import STAGE_PUBLISHED, Article
from wjs.advanced_admin.admin import ArticleAdmin, advanced_admin_site

STATE_CHANGE_WARNING = "It does not run the workflow transition"


def _request(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


def _model_admin():
    return ArticleWorkflowAdmin(ArticleWorkflowProxy, advanced_admin_site)


def _filtered(model_admin, user, params):
    """
    Return the rows of a page with the filter query parameters applied, as the changelist does.

    Django applies a lookup on a model field even without a filter for it, so the parameters are first checked
    against those the page's filters expect.
    """
    request = RequestFactory().get("/", params)
    request.user = user
    changelist = model_admin.get_changelist_instance(request)
    expected = {param for spec in changelist.filter_specs for param in spec.expected_parameters()}
    assert set(params) <= expected, f"{sorted(set(params) - expected)} belong to no filter of the page"
    return changelist.queryset


@pytest.fixture
def withdrawn_and_other(submitted_article, article):
    """Return a withdrawn workflow and a workflow in another state."""
    withdrawn = ArticleWorkflowProxy.objects.get(article=submitted_article)
    withdrawn.state = ArticleWorkflow.ReviewStates.WITHDRAWN
    withdrawn.save()
    other = ArticleWorkflowProxy.objects.get(article=article)
    assert other.state != ArticleWorkflow.ReviewStates.WITHDRAWN, "the other workflow must not be withdrawn"
    return withdrawn, other


@pytest.fixture
def searchable_workflow(submitted_article):
    """Give the article a known title and a correspondence author whose last name has a diacritic."""
    submitted_article.title = "Interpreting the signals of science"
    submitted_article.save()
    author = submitted_article.correspondence_author
    author.last_name = "Rudziński"
    author.save()
    return ArticleWorkflowProxy.objects.get(article=submitted_article)


@pytest.mark.django_db
@pytest.mark.parametrize("term", ["{pk}", "JCOM_{pk}", "Interpreting", "Rudzinski", "{owner_email}"])
def test_article_workflow_search_finds(searchable_workflow, admin, term):
    """Article pk, label, title, unaccented author name and owner email find the workflow."""
    article = searchable_workflow.article
    term = term.format(pk=article.pk, owner_email=article.owner.email)
    queryset, _ = _model_admin().get_search_results(_request(admin), ArticleWorkflowProxy.objects.all(), term)
    assert searchable_workflow in queryset, f"{term!r} must find the article workflow"


@pytest.mark.django_db
def test_article_workflow_list_filter(admin):
    """The Article workflows page offers the shared filters, with the local workflow state."""
    assert _model_admin().get_list_filter(_request(admin)) == [
        "article__journal",
        "article__stage",
        "state",
        "article__is_import",
        "article__peer_reviewed",
        "article__date_submitted",
        "article__date_accepted",
        "article__date_declined",
        "article__date_published",
    ], "unexpected Article workflows filters"


@pytest.mark.django_db
def test_article_workflow_state_filter_applies(withdrawn_and_other, admin):
    """The workflow state filter of the Article workflows page keeps only the workflows in that state."""
    withdrawn, other = withdrawn_and_other

    result = _filtered(_model_admin(), admin, {"state__exact": ArticleWorkflow.ReviewStates.WITHDRAWN})

    assert withdrawn in result, "the state filter must keep the withdrawn workflow"
    assert other not in result, "the state filter must drop workflows in other states"


@pytest.mark.django_db
def test_article_page_workflow_state_filter_applies(withdrawn_and_other, admin):
    """The workflow state filter of the Articles page keeps only the papers whose workflow is in that state."""
    withdrawn, other = withdrawn_and_other
    model_admin = ArticleAdmin(Article, advanced_admin_site)

    result = _filtered(model_admin, admin, {"articleworkflow__state__exact": ArticleWorkflow.ReviewStates.WITHDRAWN})

    assert withdrawn.article in result, "the state filter must keep the paper with the withdrawn workflow"
    assert other.article not in result, "the state filter must drop papers whose workflow is in other states"


@pytest.mark.django_db
def test_article_workflow_article_filter_applies(withdrawn_and_other, admin):
    """The article filters of the Article workflows page filter on the workflow's article."""
    withdrawn, other = withdrawn_and_other
    Article.objects.filter(pk=withdrawn.article.pk).update(stage=STAGE_PUBLISHED)

    result = _filtered(_model_admin(), admin, {"article__stage__exact": STAGE_PUBLISHED})

    assert withdrawn in result, "the stage filter must keep the workflow of the published paper"
    assert other not in result, "the stage filter must drop the workflows of the other papers"


@pytest.mark.django_db
def test_article_workflow_changelist_and_change_page(searchable_workflow, admin, client):
    """The list and the change page render, and the change page warns about the raw state edit."""
    client.force_login(admin)
    changelist_url = reverse("admin:wjs_review_articleworkflowproxy_changelist", current_app="advanced_admin")
    change_url = reverse(
        "admin:wjs_review_articleworkflowproxy_change", args=[searchable_workflow.pk], current_app="advanced_admin"
    )

    changelist_response = client.get(changelist_url)
    change_response = client.get(change_url)

    assert changelist_response.status_code == 200, f"changelist returned {changelist_response.status_code}"
    assert change_response.status_code == 200, f"change page returned {change_response.status_code}"
    assert STATE_CHANGE_WARNING in change_response.content.decode(), "the change page must show the warning"


@pytest.mark.django_db
def test_article_workflow_state_saved(searchable_workflow, admin, client):
    """Saving the change form writes the new state and nothing else."""
    latest_state_change = searchable_workflow.latest_state_change
    client.force_login(admin)
    change_url = reverse(
        "admin:wjs_review_articleworkflowproxy_change", args=[searchable_workflow.pk], current_app="advanced_admin"
    )

    response = client.post(change_url, {"state": ArticleWorkflow.ReviewStates.WITHDRAWN})

    assert response.status_code == 302, f"expected a redirect after save, got {response.status_code}"
    searchable_workflow.refresh_from_db()
    assert searchable_workflow.state == ArticleWorkflow.ReviewStates.WITHDRAWN, "the state was not saved"
    assert searchable_workflow.latest_state_change == latest_state_change, "latest_state_change must not change"


@pytest.mark.django_db
def test_article_workflow_no_add_no_delete(admin):
    """Workflows can't be added or deleted from the advanced admin."""
    request = _request(admin)
    assert not _model_admin().has_add_permission(request), "adding workflows must be disabled"
    assert not _model_admin().has_delete_permission(request), "deleting workflows must be disabled"
