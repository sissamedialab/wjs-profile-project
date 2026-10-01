"""Attention conditions for papers held in the Accepted state (specs#3174)."""

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client
from plugins.wjs_review import ac_service, states
from plugins.wjs_review.ac_service import ACStateEvaluator
from plugins.wjs_review.logic import WithdrawPreprint
from plugins.wjs_review.logic__production import ConfirmProductionReadiness
from plugins.wjs_review.management.commands.rollback_acceptance import rollback_accepted
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    WjsEditorAssignment,
)
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _accept_article

HOLD_IN_ACCEPTED = "wjs_review.events.checks.always_reject"
PASS_TO_TYPESETTER = "wjs_review.events.checks_after_acceptance.always_pass"


def _accept_with_checks(article: Article, fake_request: HttpRequest, settings, check_function: str) -> Article:
    """Accept the article with the given acceptance check function configured for its journal."""
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: [check_function]}
    fake_request.user = WjsEditorAssignment.objects.get_current(article).editor
    _accept_article(fake_request, article)
    article.refresh_from_db()
    return article


def _requires_attention(article: Article, user) -> str:
    state_cls = getattr(states, article.articleworkflow.state)
    return state_cls.article_requires_attention(article=article, user=user)


@pytest.mark.django_db
def test_access_mode_to_check_set_for_eo_when_held_in_accepted(assigned_article, fake_request, settings):
    """A paper held in Accepted by the acceptance checks gets the AC, for the EO only."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED

    eo = get_eo_user(article)
    editor = WjsEditorAssignment.objects.get_current(article).editor
    author = article.correspondence_author
    assert (
        AttentionCondition.objects.active()
        .filter(article=article, user=eo, code=ac_service.ACCESS_MODE_TO_CHECK)
        .exists()
    )
    assert _requires_attention(article, eo) == "Access mode to check"
    assert _requires_attention(article, editor) != "Access mode to check"
    assert _requires_attention(article, author) != "Access mode to check"


@pytest.mark.django_db
def test_access_mode_to_check_not_set_when_checks_pass(assigned_article, fake_request, settings):
    """A paper that passes the acceptance checks goes to ReadyForTypesetter without the AC."""
    article = _accept_with_checks(assigned_article, fake_request, settings, PASS_TO_TYPESETTER)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert not AttentionCondition.objects.filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()


@pytest.mark.django_db
def test_evaluator_creates_access_mode_to_check_for_accepted(assigned_article, fake_request, settings):
    """The evaluator (nightly rebuild) creates the AC for papers already in Accepted."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    AttentionCondition.objects.filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).delete()

    ACStateEvaluator(state=ArticleWorkflow.ReviewStates.ACCEPTED, article=article).evaluate_all()

    assert (
        AttentionCondition.objects.active()
        .filter(article=article, user=get_eo_user(article), code=ac_service.ACCESS_MODE_TO_CHECK)
        .exists()
    )


def _confirm_url(article: Article) -> str:
    return (
        f"/{article.journal.code}/plugins/wjs-review-articles/confirm_production_ready/{article.articleworkflow.pk}/"
    )


@pytest.mark.django_db
def test_confirm_production_readiness_resolves_ac(assigned_article, fake_request, settings):
    """The EO confirmation moves the paper to ReadyForTypesetter and resolves the AC."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    eo = get_eo_user(article)

    workflow = ConfirmProductionReadiness(workflow=article.articleworkflow, user=eo).run()

    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert (
        not AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()
    )


@pytest.mark.django_db
def test_confirm_production_readiness_refused_for_non_eo(assigned_article, fake_request, settings):
    """A non-EO user cannot confirm: state unchanged, AC still active."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)

    with pytest.raises(ValueError):
        ConfirmProductionReadiness(workflow=article.articleworkflow, user=article.correspondence_author).run()

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED
    assert AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()


@pytest.mark.django_db
def test_confirm_view_success_and_double_submit(assigned_article, fake_request, settings, client: Client):
    """The view keeps its messages; a second POST shows the error instead of raising."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    client.force_login(get_eo_user(article))

    response = client.post(_confirm_url(article))
    assert response.status_code == 302
    assert "Article confirmed as ready for typesetter." in [str(m) for m in get_messages(response.wsgi_request)]
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert (
        not AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()
    )

    response = client.post(_confirm_url(article))
    assert response.status_code == 302
    assert "This article cannot transition to Ready for Typesetter in its current state." in [
        str(m) for m in get_messages(response.wsgi_request)
    ]


@pytest.mark.django_db
def test_withdraw_from_accepted_resolves_ac(assigned_article, fake_request, settings):
    """Withdrawing a paper held in Accepted resolves the AC (existing resolve-all)."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    fake_request.user = article.correspondence_author

    WithdrawPreprint(
        workflow=article.articleworkflow,
        request=fake_request,
        form_data={"notification_subject": "Test subject", "notification_body": "Test body"},
    ).run()

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.WITHDRAWN
    assert (
        not AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()
    )


@pytest.mark.django_db
def test_confirm_production_readiness_resolves_ac_of_former_eo(assigned_article, fake_request, settings, normal_user):
    """The AC is resolved for anyone, also for a user who no longer holds the EO role."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    former_eo = normal_user.janeway_account
    ac_service.upsert_ac(article, former_eo, ac_service.ACCESS_MODE_TO_CHECK, "Access mode to check")

    ConfirmProductionReadiness(workflow=article.articleworkflow, user=get_eo_user(article)).run()

    assert (
        not AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()
    )


@pytest.mark.django_db
def test_rollback_acceptance_resolves_access_mode_to_check(assigned_article, fake_request, settings):
    """Rolling back an acceptance moves the paper out of Accepted: its AC no longer applies."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()

    rollback_accepted(article)

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert (
        not AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()
    )
