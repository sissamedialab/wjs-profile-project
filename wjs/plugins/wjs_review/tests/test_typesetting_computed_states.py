"""Tests for the computed states of articles in the typesetter's hands (specs#3120)."""

import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test.client import Client
from django.urls import reverse
from submission.models import Article
from typesetting.models import TypesettingRound

from wjs.jcom_profile.models import JCOMProfile

from ..models import ArticleWorkflow


def _fresh_workflow(article: Article) -> ArticleWorkflow:
    """Return a freshly loaded workflow, so that cached properties are recomputed."""
    return ArticleWorkflow.objects.get(pk=article.articleworkflow.pk)


@pytest.mark.django_db
def test_first_round_is_taken_in_charge(assigned_to_typesetter_article: Article):
    """A paper just assigned to the typesetter (round 1) is "Taken in charge"."""
    workflow = _fresh_workflow(assigned_to_typesetter_article)
    assert workflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED, "Paper must be with the typesetter"
    assert (
        workflow.state_value == ArticleWorkflow.ReviewComputedStates.TAKEN_IN_CHARGE
    ), "Round 1 must be computed as taken in charge"
    assert workflow.state_label == "Taken in charge", "Round 1 must be labelled 'Taken in charge'"


@pytest.mark.django_db
def test_author_corrections_are_back_to_typesetter(assigned_to_typesetter_proofs_done_article: Article):
    """A paper back from the author's proofs (round 2) is "Back to typesetter"."""
    article = assigned_to_typesetter_proofs_done_article
    workflow = _fresh_workflow(article)
    latest_round = TypesettingRound.objects.filter(article=article).first()
    assert latest_round.round_number == 2, "Author corrections must open round 2"
    assert (
        workflow.state_value == ArticleWorkflow.ReviewComputedStates.BACK_TO_TYPESETTER
    ), "Round > 1 must be computed as back to typesetter"
    assert workflow.state_label == "Back to typesetter", "Round > 1 must be labelled 'Back to typesetter'"


@pytest.mark.django_db
def test_eo_send_back_is_back_to_typesetter(stage_proofing_article: Article, client: Client, eo_user: JCOMProfile):
    """A paper sent back by the EO from "Ready for publication" is "Back to typesetter"."""
    article = stage_proofing_article
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION
    article.articleworkflow.save()
    client.force_login(eo_user.janeway_account)
    url = reverse("wjs_send_back_to_typ", kwargs={"pk": article.articleworkflow.pk})
    response = client.post(url, data={"subject": "Back to typesetter", "body": "Please fix this."})
    assert response.status_code == 302, "EO send-back must succeed"
    workflow = _fresh_workflow(article)
    assert workflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED, "Paper must be with the typesetter"
    assert (
        workflow.state_value == ArticleWorkflow.ReviewComputedStates.BACK_TO_TYPESETTER
    ), "EO send-back must be computed as back to typesetter"


@pytest.mark.django_db
def test_no_typesetting_round_is_taken_in_charge(assigned_to_typesetter_article: Article):
    """A paper with the typesetter but without any typesetting round falls back to "Taken in charge"."""
    TypesettingRound.objects.filter(article=assigned_to_typesetter_article).delete()
    workflow = _fresh_workflow(assigned_to_typesetter_article)
    assert workflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED, "Paper must be with the typesetter"
    assert (
        workflow.state_value == ArticleWorkflow.ReviewComputedStates.TAKEN_IN_CHARGE
    ), "Missing rounds must fall back to taken in charge"


@pytest.mark.django_db
def test_latest_round_decides(assigned_to_typesetter_article: Article):
    """The highest round number decides, regardless of other (e.g. round 0) rounds."""
    TypesettingRound.objects.create(article=assigned_to_typesetter_article, round_number=0)
    TypesettingRound.objects.create(article=assigned_to_typesetter_article, round_number=2)
    workflow = _fresh_workflow(assigned_to_typesetter_article)
    assert (
        workflow.state_value == ArticleWorkflow.ReviewComputedStates.BACK_TO_TYPESETTER
    ), "Latest round 2 must be computed as back to typesetter"


@pytest.mark.django_db
def test_proofreading_keeps_raw_state(stage_proofing_article: Article):
    """Typesetting rounds do not affect states other than TypesetterSelected."""
    workflow = _fresh_workflow(stage_proofing_article)
    assert (
        workflow.state_value == ArticleWorkflow.ReviewStates.PROOFREADING
    ), "Proofreading must keep its raw state value"


@pytest.mark.django_db
def test_review_states_unchanged(assigned_article: Article):
    """Review-phase computed states are not affected by the typesetting branch."""
    workflow = _fresh_workflow(assigned_article)
    assert (
        workflow.state_value == ArticleWorkflow.ReviewComputedStates.ASSIGNED_TO_EDITOR
    ), "An article with an editor and no reviews must still be 'assigned to editor'"


@pytest.mark.django_db
def test_typesetter_working_on_shows_version(
    assigned_to_typesetter_proofs_done_article: Article, typesetter: JCOMProfile, client: Client
):
    """The typesetter's "Working on" list shows the production version and the computed label."""
    article = assigned_to_typesetter_proofs_done_article
    client.force_login(typesetter.janeway_account)
    response = client.get(reverse("wjs_review_typesetter_workingon"))
    assert response.status_code == 200, "Typesetter must be able to open the Working on list"
    content = response.content.decode()
    assert f"{article.pk}/v2" in content, "Working on list must show the production version"
    assert "Back to typesetter" in content, "Working on list must show the computed label"


@pytest.mark.django_db
def test_pending_issues_row_shows_version(
    assigned_to_typesetter_article: Article, typesetter: JCOMProfile, fake_request: HttpRequest
):
    """A "Pending issues" article row shows the production version beside the article id."""
    article = assigned_to_typesetter_article
    fake_request.user = typesetter.janeway_account
    content = render_to_string(
        "wjs_review/lists/elements/issue/table_article.html",
        {"article": article, "workflow": article.articleworkflow, "issue": None},
        request=fake_request,
    )
    assert f"{article.pk}/v1" in content, "Pending issues row must show the production version"
