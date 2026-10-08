"""Timeline message for the Accepted -> Ready for typesetter transition (specs#3178)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from plugins.wjs_review import ac_service
from plugins.wjs_review.communication_utils import get_system_user
from plugins.wjs_review.logic__production import ConfirmProductionReadiness
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    Message,
    WjsEditorAssignment,
)
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _accept_article

HOLD_IN_ACCEPTED = "wjs_review.events.checks.always_reject"
PASS_TO_TYPESETTER = "wjs_review.events.checks_after_acceptance.always_pass"
SUBJECT = "Paper ready for typesetter"


def _accept_with_checks(article: Article, fake_request: HttpRequest, settings, check_function: str) -> Article:
    """Accept the article with the given acceptance check function configured for its journal."""
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: [check_function]}
    fake_request.user = WjsEditorAssignment.objects.get_current(article).editor
    # Keep the messages logged during acceptance: they are what we assert on.
    _accept_article(fake_request, article, cleanup_side_effects=False)
    article.refresh_from_db()
    return article


def _ready_messages(article: Article):
    return Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.pk,
        subject=SUBJECT,
    )


@pytest.mark.django_db
def test_automatic_transition_logs_timeline_message(assigned_article, fake_request, settings):
    """Checks pass: the system moves the paper and logs one timeline message."""
    article = _accept_with_checks(assigned_article, fake_request, settings, PASS_TO_TYPESETTER)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER, "checks should pass"

    messages = _ready_messages(article)
    assert messages.count() == 1, "exactly one ready-for-typesetter message expected"
    message = messages.get()
    assert message.verbosity == Message.MessageVerbosity.TIMELINE, "message must be timeline-only"
    assert message.actor == get_system_user(article.journal), "automatic transition is done by the system"
    assert "verified" in message.body, "body should state the automatic verification"
    assert list(message.recipients.all()) == [get_eo_user(article)], "EO user is the only recipient"
    assert message.read_by_eo, "message must be flagged as read by EO"
    assert (
        not AttentionCondition.objects.active()
        .filter(article=article, user=get_eo_user(article), code=ac_service.HAS_UNREAD_MESSAGE)
        .exists()
    ), "an informational message must not raise an unread-message AC for the EO"


@pytest.mark.django_db
def test_held_in_accepted_logs_no_ready_message(assigned_article, fake_request, settings):
    """Checks fail: the paper stays in Accepted and no ready-for-typesetter message is logged."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "checks should hold the paper"
    assert not _ready_messages(article).exists(), "no ready-for-typesetter message expected"


@pytest.mark.django_db
def test_eo_confirmation_logs_timeline_message(assigned_article, fake_request, settings):
    """Manual confirmation: one timeline message, actor is the confirming user."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    eo = get_eo_user(article)

    ConfirmProductionReadiness(workflow=article.articleworkflow, user=eo).run()

    messages = _ready_messages(article)
    assert messages.count() == 1, "exactly one ready-for-typesetter message expected"
    message = messages.get()
    assert message.verbosity == Message.MessageVerbosity.TIMELINE, "message must be timeline-only"
    assert message.actor == eo, "actor must be the confirming user"
    assert f"confirmed by {eo.full_name()}" in message.body, "body should name the confirming user"
