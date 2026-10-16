"""wjapp import and the JCAP production-readiness hold (specs#3131)."""

import datetime

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from identifiers.models import Identifier
from plugins.wjs_review import ac_service
from plugins.wjs_review.events.checks_after_acceptance import (
    render_production_readiness_subject,
)
from plugins.wjs_review.logic__production import (
    AssignTypesetter,
    SendProductionXMLToPublisher,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    Message,
    MessageRecipients,
    WjsEditorAssignment,
)
from submission.models import Article

from wjs.jcom_profile import import_production_hold

from .conftest import _accept_article

JCAP_CHECK = "plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed"
READY_FOR_TYPESETTER_SUBJECT = "Paper ready for typesetter"


@pytest.fixture
def publisher_sends(monkeypatch) -> list:
    """Record every attempt to send the production zip to the publisher."""
    calls = []

    def fake_run(self, **kwargs):
        calls.append(self.articleworkflow.article.pk)
        return self.articleworkflow

    monkeypatch.setattr(SendProductionXMLToPublisher, "run", fake_run)
    return calls


def _held_imported_article(article: Article, fake_request, settings) -> Article:
    """Accept an imported paper with the JCAP check configured: it is held in ACCEPTED."""
    Identifier.objects.create(identifier="JCAP_001P_1025", article=article, id_type="preprintid", enabled=True)
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: [JCAP_CHECK]}
    fake_request.user = WjsEditorAssignment.objects.get_current(article).editor
    # Keep the messages logged at acceptance: the finalize tests assert on them.
    _accept_article(fake_request, article, cleanup_side_effects=False)
    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "precondition: paper held"
    return article


def _active_hold_ac(article: Article):
    return AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK)


def _messages(article: Article, subject: str):
    return Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article), object_id=article.pk, subject=subject
    )


@pytest.mark.django_db
def test_release_moves_held_paper_to_ready_for_typesetter(assigned_article, fake_request, settings, publisher_sends):
    """The history goes on after acceptance: the held paper is let through like on the automatic path."""
    article = _held_imported_article(assigned_article, fake_request, settings)

    assert import_production_hold.release_production_readiness_hold(article) is True, "A held paper must be released"

    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER, "Must reach RFT"
    assert not _active_hold_ac(article).exists(), "The hold AC must be resolved"
    assert publisher_sends == [article.pk], "The production zip must be sent once, as on the automatic path"
    assert _messages(article, READY_FOR_TYPESETTER_SUBJECT).count() == 1, "One ready-for-typesetter timeline message"


@pytest.mark.django_db
def test_release_lets_typesetter_take_in_charge(assigned_article, fake_request, settings, typesetter, publisher_sends):
    """After the release, the replayed TYP_TAKES_CHARGE (AssignTypesetter) succeeds."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    import_production_hold.release_production_readiness_hold(article)

    fake_request.user = typesetter
    AssignTypesetter(article=article, typesetter=typesetter, request=fake_request).run()

    article.refresh_from_db()
    assert (
        article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
    ), "The replay must go on into production"


@pytest.mark.django_db
def test_release_does_nothing_when_not_accepted(assigned_article, publisher_sends):
    """A paper that is not in ACCEPTED is left alone."""
    state_before = assigned_article.articleworkflow.state

    assert import_production_hold.release_production_readiness_hold(assigned_article) is False, "Nothing to release"

    assigned_article.refresh_from_db()
    assert assigned_article.articleworkflow.state == state_before, "State must not change"
    assert publisher_sends == [], "Nothing must be sent"
    assert not _messages(assigned_article, READY_FOR_TYPESETTER_SUBJECT).exists(), "No timeline message"


@pytest.mark.django_db
def test_finalize_keeps_held_paper_message_unread(assigned_article, fake_request, settings):
    """History stopped at acceptance: the JCAP message is unread for the EO and the AC stays active."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    import_production_hold.mark_all_messages_read(article)

    import_production_hold.finalize_production_readiness_hold(article)

    jcap_messages = _messages(article, render_production_readiness_subject(article))
    assert jcap_messages.exists(), "precondition: the JCAP message was sent"
    assert not jcap_messages.filter(read_by_eo=True).exists(), "JCAP message must be unread for the EO"
    assert not MessageRecipients.objects.filter(
        message__in=jcap_messages, read=True
    ).exists(), "JCAP message must be unread for its recipients"
    other_messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article), object_id=article.pk
    ).exclude(pk__in=jcap_messages)
    assert not other_messages.filter(read_by_eo=False).exists(), "Other messages must stay read"
    assert _active_hold_ac(article).exists(), "The hold AC must stay active"


@pytest.mark.django_db
def test_finalize_resolves_ac_when_paper_moved_on(assigned_article, fake_request, settings):
    """A paper forced to PUBLISHED by the import (incomplete history) keeps no hold AC."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    workflow = article.articleworkflow
    workflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    workflow.save()

    import_production_hold.finalize_production_readiness_hold(article)

    assert not _active_hold_ac(article).exists(), "The hold AC must be resolved"


@pytest.mark.django_db
def test_release_is_dated_at_the_wjapp_action(assigned_article, fake_request, settings, publisher_sends):
    """The release is dated like the replayed wjapp step, not when the import runs."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    action_date = timezone.make_aware(datetime.datetime(2025, 3, 4, 10, 30), datetime.timezone.utc)

    import_production_hold.release_production_readiness_hold(article, when=action_date)

    message = _messages(article, READY_FOR_TYPESETTER_SUBJECT).get()
    assert message.created == action_date, "The timeline message must carry the wjapp action date"
