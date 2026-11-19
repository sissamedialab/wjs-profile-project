"""Tests for the EO action that resends a failed production zip to the publisher (wjs/specs#3450)."""

import threading
from unittest import mock

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.client import Client
from django.urls import reverse
from plugins.wjs_review import ac_service, states
from plugins.wjs_review.metadata_export.sftp import SFTPSendError
from submission.models import STAGE_ACCEPTED, STAGE_TYPESETTING, Article

from wjs.jcom_profile.utils import get_eo_user

from .. import conditions
from ..logic__production import (
    ResendProductionXMLToPublisher,
    SendProductionXMLToPublisher,
)
from ..models import ArticleWorkflow, AttentionCondition

SEND_FUNCTION = "plugins.wjs_review.metadata_export.publishers.send_zip_to_iop"
LOG_OPERATION = "plugins.wjs_review.logic__production.communication_utils.log_operation"
BUILD_ZIP = "plugins.wjs_review.logic__production.build_production_export_zip"
ACTION_NAME = "resend production zip to publisher"
WAIT = 60
ACTION_STATES = [
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PROOFREADING,
]


def _flag_send_failure(article: Article, message: str = "connection refused") -> None:
    """Raise the EO attention condition left by a failed first send."""
    ac_service.upsert_for_role(
        article,
        "eo",
        ac_service.PRODUCTION_EXPORT_SEND_FAILED,
        message=message,
        priority=ac_service.PRODUCTION_EXPORT_SEND_FAILED_PRIORITY,
    )


@pytest.mark.django_db
def test_production_export_send_failed_true_while_active(accepted_article: Article):
    """The condition applies while the send-failure attention condition is active."""
    _flag_send_failure(accepted_article)

    assert ac_service.production_export_send_failed(accepted_article), "active attention condition must apply"


@pytest.mark.django_db
def test_production_export_send_failed_true_when_dismissed(accepted_article: Article):
    """A dismissed attention condition still applies: the EO only set it aside."""
    _flag_send_failure(accepted_article)
    AttentionCondition.objects.filter(article=accepted_article, code=ac_service.PRODUCTION_EXPORT_SEND_FAILED).update(
        status=AttentionCondition.Status.DISMISSED
    )

    assert ac_service.production_export_send_failed(accepted_article), "dismissed attention condition must apply"


@pytest.mark.django_db
def test_production_export_send_failed_false_when_resolved(accepted_article: Article):
    """A resolved attention condition no longer applies."""
    _flag_send_failure(accepted_article)
    ac_service.resolve_for_role(accepted_article, "eo", ac_service.PRODUCTION_EXPORT_SEND_FAILED)

    assert not ac_service.production_export_send_failed(
        accepted_article
    ), "resolved attention condition must not apply"


@pytest.mark.django_db
def test_production_export_send_failed_false_without_ac(accepted_article: Article):
    """Without any attention condition the condition does not apply."""
    assert not ac_service.production_export_send_failed(accepted_article), "no attention condition, nothing to resend"


@pytest.fixture
def sendable_article(accepted_article: Article, settings) -> Article:
    """Return an accepted article whose journal sends the production zip, after a failed first send."""
    settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS = {accepted_article.journal.code: SEND_FUNCTION}
    accepted_article.manuscript_files.clear()
    ArticleWorkflow.objects.filter(pk=accepted_article.articleworkflow.pk).update(
        state=ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    )
    accepted_article.articleworkflow.refresh_from_db()
    _flag_send_failure(accepted_article)
    return accepted_article


def _assert_button_matches_logic(article: Article, expect_resendable: bool) -> None:
    """The action condition and the logic requirements must always agree (MR !1643 review)."""
    workflow = article.articleworkflow
    shown = conditions.can_resend_production_zip(workflow, get_eo_user(article))
    try:
        ResendProductionXMLToPublisher(articleworkflow=workflow)._check_requirements()
        accepted = True
    except ValidationError:
        accepted = False
    assert shown is expect_resendable, f"button visibility must be {expect_resendable}"
    assert accepted is expect_resendable, f"logic acceptance must be {expect_resendable}"


@pytest.mark.django_db
def test_resend_possible_in_production_state_with_failure_and_send_function(sendable_article: Article):
    """All requirements met: the button shows and the logic accepts."""
    assert conditions.production_zip_resend_blocker(sendable_article.articleworkflow) is None
    _assert_button_matches_logic(sendable_article, expect_resendable=True)


@pytest.mark.django_db
def test_resend_blocked_without_failure(sendable_article: Article):
    """Resolved failure: no button, and the logic refuses."""
    ac_service.resolve_for_role(sendable_article, "eo", ac_service.PRODUCTION_EXPORT_SEND_FAILED)
    _assert_button_matches_logic(sendable_article, expect_resendable=False)


@pytest.mark.django_db
def test_resend_blocked_without_send_function(sendable_article: Article, settings):
    """A leftover failure on a journal with no send function: no button, and the logic refuses."""
    settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS = {}
    _assert_button_matches_logic(sendable_article, expect_resendable=False)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [
        ArticleWorkflow.ReviewStates.ACCEPTED,
        ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
        ArticleWorkflow.ReviewStates.PUBLISHED,
    ],
)
def test_resend_blocked_outside_production_states(sendable_article: Article, state):
    """A failure surviving outside production (e.g. after rollback_acceptance): no button, and the logic refuses."""
    ArticleWorkflow.objects.filter(pk=sendable_article.articleworkflow.pk).update(state=state)
    sendable_article.articleworkflow.refresh_from_db()
    _assert_button_matches_logic(sendable_article, expect_resendable=False)


def _send_failure_acs(article: Article):
    return AttentionCondition.objects.filter(article=article, code=ac_service.PRODUCTION_EXPORT_SEND_FAILED)


def test_action_declared_exactly_in_resend_states():
    """The states offering the action must match the states the shared check accepts."""
    declaring = {
        state
        for state in ArticleWorkflow.ReviewStates.values
        if getattr(states, state, None) is not None
        and states.RESEND_PRODUCTION_ZIP_ACTION in getattr(states, state).article_actions
    }
    assert declaring == set(conditions.RESEND_PRODUCTION_ZIP_STATES), "action states and check states diverge"


@pytest.mark.django_db
def test_resend_success_logs_message_and_resolves_ac(sendable_article: Article):
    """A successful resend sends once, logs the EO message and resolves the attention condition."""
    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION) as mock_log:
        result = ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    assert result == sendable_article.articleworkflow
    mock_send.assert_called_once()
    mock_log.assert_called()
    statuses = set(_send_failure_acs(sendable_article).values_list("status", flat=True))
    assert statuses == {AttentionCondition.Status.RESOLVED}, "every send-failure row must be resolved"


@pytest.mark.django_db
def test_resend_works_after_the_article_left_the_accepted_stage(sendable_article: Article):
    """The export only supports accepted articles; a resend after typesetting started must still build the zip."""
    Article.objects.filter(pk=sendable_article.pk).update(stage=STAGE_TYPESETTING)
    sendable_article.refresh_from_db()
    assert sendable_article.stage == STAGE_TYPESETTING, "precondition: the article is past the accepted stage"

    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION):
        ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    mock_send.assert_called_once()
    assert sendable_article.articleworkflow.article.stage == STAGE_TYPESETTING, "the stage must be restored in memory"
    sendable_article.refresh_from_db()
    assert sendable_article.stage == STAGE_TYPESETTING, "the stage must never be persisted as accepted"
    assert sendable_article.stage != STAGE_ACCEPTED


@pytest.mark.django_db
def test_resend_resolves_leftover_rows_of_users_who_are_not_eo_anymore(sendable_article: Article, typesetter):
    """A stale row of a former EO must not keep the action visible after a good resend."""
    ac_service.upsert_ac(
        sendable_article,
        typesetter,
        ac_service.PRODUCTION_EXPORT_SEND_FAILED,
        message="stale",
        priority=ac_service.PRODUCTION_EXPORT_SEND_FAILED_PRIORITY,
    )

    with mock.patch(SEND_FUNCTION), mock.patch(LOG_OPERATION):
        ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    assert not ac_service.production_export_send_failed(sendable_article), "no row may keep applying after a resend"


@pytest.mark.django_db
def test_resend_failure_keeps_ac_with_new_message(sendable_article: Article):
    """A failed resend raises, but keeps the attention condition with the new error (Review Focus 1)."""
    with mock.patch(SEND_FUNCTION, side_effect=SFTPSendError("still refused")), mock.patch(LOG_OPERATION):
        with pytest.raises(ValidationError, match="still refused"):
            ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    ac = _send_failure_acs(sendable_article).get(user=get_eo_user(sendable_article))
    assert ac.status == AttentionCondition.Status.ACTIVE
    assert "still refused" in ac.message, "the refreshed message must survive the error"


@pytest.mark.django_db
def test_resend_with_dismissed_ac_sends(sendable_article: Article):
    """A dismissed attention condition still applies, so the resend goes ahead (Review Focus 2)."""
    _send_failure_acs(sendable_article).update(status=AttentionCondition.Status.DISMISSED)

    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION):
        ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    mock_send.assert_called_once()


@pytest.mark.django_db
def test_resend_without_active_ac_raises_and_does_not_send(sendable_article: Article):
    """Nothing to resend (also a second click after a success): ValidationError, no send (Review Focus 3)."""
    ac_service.resolve_for_role(sendable_article, "eo", ac_service.PRODUCTION_EXPORT_SEND_FAILED)

    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION):
        with pytest.raises(ValidationError):
            ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    mock_send.assert_not_called()


@pytest.mark.django_db
def test_resend_without_send_function_raises(sendable_article: Article, settings):
    """A journal with no send function cannot resend, even with a leftover attention condition (Review Focus 4)."""
    settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS = {}

    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION):
        with pytest.raises(ValidationError):
            ResendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow).run()

    mock_send.assert_not_called()


def _resend_in_thread(workflow_pk: int, results: dict, key: str) -> None:
    """Run a resend in its own thread, hence in its own DB connection, and record the outcome."""
    try:
        workflow = ArticleWorkflow.objects.get(pk=workflow_pk)
        results[key] = ResendProductionXMLToPublisher(articleworkflow=workflow).run()
    except Exception as e:  # noqa: BLE001
        results[key] = e
    finally:
        connection.close()


@pytest.mark.django_db(transaction=True)
def test_simultaneous_resends_send_once(sendable_article: Article):
    """Two resends at the same time: the second waits for the first, finds the failure resolved and is refused."""
    a_sending = threading.Event()
    release_a = threading.Event()

    def send(article, zip_bytes):
        # Resend A holds the workflow row lock while it is sending: resend B starts in that window.
        if threading.current_thread().name == "resend-A":
            a_sending.set()
            assert release_a.wait(WAIT), "resend A was never released"

    workflow_pk = sendable_article.articleworkflow.pk
    results = {}
    with mock.patch(SEND_FUNCTION, side_effect=send) as mock_send, mock.patch(LOG_OPERATION):
        thread_a = threading.Thread(target=_resend_in_thread, args=(workflow_pk, results, "A"), name="resend-A")
        thread_a.start()
        assert a_sending.wait(WAIT), f"resend A never reached the send: {results.get('A')!r}"
        thread_b = threading.Thread(target=_resend_in_thread, args=(workflow_pk, results, "B"), name="resend-B")
        thread_b.start()
        thread_b.join(3)
        b_was_blocked = thread_b.is_alive()
        release_a.set()
        thread_a.join(WAIT)
        thread_b.join(WAIT)

    assert not isinstance(results["A"], Exception), f"resend A must succeed: {results['A']!r}"
    assert b_was_blocked, "resend B must wait for the lock held by resend A"
    assert isinstance(results["B"], ValidationError), f"resend B must be refused: {results['B']!r}"
    assert "no failed delivery" in str(results["B"]), f"unexpected refusal reason: {results['B']!r}"
    assert mock_send.call_count == 1, "the zip must be sent once"


@pytest.mark.django_db
def test_sender_records_send_error_only_on_swallowed_failure(sendable_article: Article):
    """The sender exposes the swallowed error, and nothing when the send worked."""
    sender = SendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow)
    with mock.patch(SEND_FUNCTION), mock.patch(LOG_OPERATION):
        sender.run()
    assert sender.send_error is None

    sender = SendProductionXMLToPublisher(articleworkflow=sendable_article.articleworkflow)
    with mock.patch(SEND_FUNCTION, side_effect=SFTPSendError("boom")), mock.patch(LOG_OPERATION):
        sender.run()
    assert sender.send_error == "boom"


@pytest.fixture
def failed_send_article(assigned_to_typesetter_article: Article, settings) -> Article:
    """Return a paper in production whose first zip send failed, for a journal that sends the zip."""
    settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS = {assigned_to_typesetter_article.journal.code: SEND_FUNCTION}
    _flag_send_failure(assigned_to_typesetter_article)
    return assigned_to_typesetter_article


def _available_action_names(workflow: ArticleWorkflow, user) -> list[str]:
    state_class = states.BaseState.get_state_class(workflow)
    return [action.name for action in state_class.article_actions if action.is_available(workflow, user)]


def _resend_url(workflow: ArticleWorkflow) -> str:
    return reverse("wjs-resend-production-zip", kwargs={"pk": workflow.pk})


@pytest.mark.django_db
@pytest.mark.parametrize("state", ACTION_STATES)
def test_action_available_to_eo_only_while_ac_applies(failed_send_article: Article, eo_user, typesetter, state):
    """The resend action shows to the EO in the typesetting states, only while the failure applies."""
    workflow = failed_send_article.articleworkflow
    ArticleWorkflow.objects.filter(pk=workflow.pk).update(state=state)
    workflow.refresh_from_db()

    assert ACTION_NAME in _available_action_names(workflow, eo_user), f"EO must see the action in {state}"
    assert ACTION_NAME not in _available_action_names(workflow, typesetter), "only the EO may resend"

    ac_service.resolve_for_role(failed_send_article, "eo", ac_service.PRODUCTION_EXPORT_SEND_FAILED)
    assert ACTION_NAME not in _available_action_names(workflow, eo_user), "no action once the failure is resolved"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION, ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS],
)
def test_action_not_available_once_publication_starts(failed_send_article: Article, eo_user, state):
    """The resend action is hidden from ReadyForPublication onwards, even while the failure applies."""
    workflow = failed_send_article.articleworkflow
    ArticleWorkflow.objects.filter(pk=workflow.pk).update(state=state)
    workflow.refresh_from_db()

    assert ACTION_NAME not in _available_action_names(workflow, eo_user), f"EO must not see the action in {state}"


@pytest.mark.django_db
def test_view_eo_post_resends_and_redirects(failed_send_article: Article, eo_user, client: Client):
    """The EO resends from the view: one send, a success message, redirect to the article page."""
    workflow = failed_send_article.articleworkflow
    client.force_login(eo_user)

    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION), mock.patch(BUILD_ZIP, return_value=b"zip"):
        response = client.post(_resend_url(workflow))

    assert response.status_code == 302, "EO request must redirect"
    assert response.headers["Location"] == reverse("wjs_article_details", kwargs={"pk": workflow.pk})
    mock_send.assert_called_once()
    assert [str(m) for m in get_messages(response.wsgi_request)], "EO must get a confirmation message"
    assert not ac_service.production_export_send_failed(failed_send_article), "the failure must be resolved"


@pytest.mark.django_db
def test_view_failed_resend_flashes_error(failed_send_article: Article, eo_user, client: Client):
    """A failed resend shows the SFTP error and keeps the attention condition."""
    workflow = failed_send_article.articleworkflow
    client.force_login(eo_user)

    with (
        mock.patch(SEND_FUNCTION, side_effect=SFTPSendError("still refused")),
        mock.patch(LOG_OPERATION),
        mock.patch(BUILD_ZIP, return_value=b"zip"),
    ):
        response = client.post(_resend_url(workflow))

    assert response.status_code == 302, "failed resend must redirect to the article page"
    assert any("still refused" in str(m) for m in get_messages(response.wsgi_request)), "error must be shown"
    assert ac_service.production_export_send_failed(failed_send_article), "the failure must still apply"


@pytest.mark.django_db
def test_view_refuses_non_eo_and_does_not_send(failed_send_article: Article, typesetter, client: Client):
    """Typesetter and anonymous users cannot resend (Review Focus 5)."""
    workflow = failed_send_article.articleworkflow

    with mock.patch(SEND_FUNCTION) as mock_send, mock.patch(LOG_OPERATION), mock.patch(BUILD_ZIP, return_value=b"zip"):
        anonymous_response = client.post(_resend_url(workflow))
        client.force_login(typesetter)
        typesetter_response = client.post(_resend_url(workflow))

    assert anonymous_response.status_code == 302, "anonymous users are sent to the login page"
    assert anonymous_response.headers["Location"] != reverse("wjs_article_details", kwargs={"pk": workflow.pk})
    assert typesetter_response.status_code == 403, "a typesetter must be refused"
    mock_send.assert_not_called()


@pytest.mark.django_db
def test_view_get_not_allowed(failed_send_article: Article, eo_user, client: Client):
    """The resend view only accepts POST."""
    client.force_login(eo_user)

    response = client.get(_resend_url(failed_send_article.articleworkflow))

    assert response.status_code == 405, "only POST is allowed"
