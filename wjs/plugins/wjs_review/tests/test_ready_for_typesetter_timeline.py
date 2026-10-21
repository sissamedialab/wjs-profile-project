"""Messages logged when an accepted paper is checked for production (specs#3178, specs#3176)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from plugins.wjs_review import ac_service
from plugins.wjs_review.communication_utils import get_system_user
from plugins.wjs_review.logic__production import (
    ConfirmProductionReadiness,
    VerifyProductionRequirements,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    Message,
    WjsEditorAssignment,
)
from plugins.wjs_submission.models import AccessMode
from plugins.wjs_submission.settings import OA_CODE_TA
from submission.models import Article
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import get_eo_user, render_template

from .conftest import _accept_article

HOLD_IN_ACCEPTED = "wjs_review.events.checks.always_reject"
PASS_TO_TYPESETTER = "wjs_review.events.checks_after_acceptance.always_pass"
SUBJECT = "Paper ready for typesetter"
ISSUES_AFTER_ACCEPTANCE = "Issues after acceptance"


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


def _article_messages(article: Article):
    return Message.objects.filter(content_type=ContentType.objects.get_for_model(article), object_id=article.pk)


def _set_ta(article: Article) -> None:
    """Give the article the transformative-agreement access mode."""
    access_mode = AccessMode.objects.filter(code=OA_CODE_TA).first() or AccessMode.objects.create(
        name="Transformative agreement", code=OA_CODE_TA
    )
    submission_data = article.submission_data
    submission_data.access_mode = access_mode
    submission_data.save()


@pytest.mark.django_db
def test_held_in_accepted_sends_no_generic_issues_message(assigned_article, fake_request, settings):
    """Checks fail: no generic "Issues after acceptance" message is sent (specs#3176)."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "checks should hold the paper"
    assert (
        not _article_messages(article).filter(subject__startswith=ISSUES_AFTER_ACCEPTANCE).exists()
    ), "the generic acceptance-issues message must not be sent"


@pytest.mark.django_db
def test_jcap_ta_paper_held_sends_one_eo_message(assigned_article, fake_request, settings):
    """A JCAP TA paper held in Accepted: the EO gets only the TA message (specs#3176)."""
    # The JCAP check functions as configured, whatever their name. Read before
    # _accept_with_checks, which replaces the whole setting.
    jcap_checks = settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    _set_ta(article)
    _article_messages(article).delete()
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: jcap_checks}
    eo = get_eo_user(article)

    VerifyProductionRequirements(article.articleworkflow).run()

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "TA paper must be held"
    eo_messages = _article_messages(article).filter(recipients=eo)
    assert eo_messages.count() == 1, "EO must receive exactly one message"
    ta_subject = render_template(
        get_setting(
            setting_group_name="wjs_review", setting_name="jcap_ta_pending_subject", journal=article.journal
        ).processed_value,
        {"article": article},
    )
    assert eo_messages.get().subject == ta_subject, "the only EO message must be the TA one"
