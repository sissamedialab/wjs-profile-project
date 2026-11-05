"""Tests for the publisher green light before publication (wjs/specs#2914)."""

from unittest import mock

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test.client import Client
from django.urls import reverse
from plugins.wjs_review import states
from plugins.wjs_review.logic__production import (
    BeginPublication,
    SetPublisherConfirmedFlag,
)
from submission.models import Article

from ..models import ArticleWorkflow

TOGGLE_STATES = [
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PROOFREADING,
    ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
]
PUBLISHER_ACTION_NAME = "toggle publisher confirmed flag"
GATE_MESSAGE = (
    "Please ensure that the publisher confirmed that this paper can be published "
    "(and click on the appropriate action)"
)


def _set_state(article: Article, state: str) -> ArticleWorkflow:
    workflow = article.articleworkflow
    workflow.state = state
    workflow.save()
    return workflow


def _available_action_names(workflow: ArticleWorkflow, user) -> list[str]:
    state_class = states.BaseState.get_state_class(workflow)
    return [action.name for action in state_class.article_actions if action.is_available(workflow, user)]


@pytest.mark.django_db
def test_requires_publisher_confirmation_follows_setting(assigned_to_typesetter_article: Article, settings):
    workflow = assigned_to_typesetter_article.articleworkflow
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    assert workflow.requires_publisher_confirmation(), "Journal listed in the setting must require confirmation"
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    assert not workflow.requires_publisher_confirmation(), "Journal not listed must not require confirmation"


@pytest.mark.django_db
def test_publisher_confirmation_missing(assigned_to_typesetter_article: Article, settings):
    workflow = assigned_to_typesetter_article.articleworkflow
    assert workflow.production_flag_iop_ready is False, "New flag must default to False"

    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    assert workflow.publisher_confirmation_missing(), "Required and not set: confirmation is missing"
    workflow.production_flag_iop_ready = True
    assert not workflow.publisher_confirmation_missing(), "Required and set: confirmation is not missing"

    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    workflow.production_flag_iop_ready = False
    assert not workflow.publisher_confirmation_missing(), "Not required: confirmation is never missing"


def test_default_setting_lists_only_jcap():
    from wjs.defaults import settings as wjs_settings

    assert wjs_settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION == ["JCAP"], "Only JCAP requires it by default"


def _set_url(workflow: ArticleWorkflow, confirmed: str) -> str:
    return reverse("wjs_set_publisher_confirmed", kwargs={"pk": workflow.pk}) + f"?confirmed={confirmed}"


@pytest.mark.django_db
@pytest.mark.parametrize("state", TOGGLE_STATES)
def test_set_and_revoke_confirmation(assigned_to_typesetter_article: Article, settings, state):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, state)

    SetPublisherConfirmedFlag(workflow=workflow, confirmed=True).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is True, f"Confirming in {state} must set the flag"

    SetPublisherConfirmedFlag(workflow=workflow, confirmed=False).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, f"Revoking in {state} must clear the flag"


@pytest.mark.django_db
@pytest.mark.parametrize("confirmed", [True, False])
def test_setting_current_value_again_is_refused(assigned_to_typesetter_article: Article, settings, confirmed):
    """A stale page asking for the value already stored must not flip it (e.g. "Revoke" on a revoked paper)."""
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    workflow.production_flag_iop_ready = confirmed
    workflow.save()

    with pytest.raises(ValueError):
        SetPublisherConfirmedFlag(workflow=workflow, confirmed=confirmed).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is confirmed, "Flag must keep its value"


@pytest.mark.django_db
def test_set_confirmation_keeps_concurrent_changes_to_other_fields(assigned_to_typesetter_article: Article, settings):
    """Only the flag is written: a concurrent galley-test result on the same row must survive."""
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    stale_workflow = assigned_to_typesetter_article.articleworkflow
    ArticleWorkflow.objects.filter(pk=stale_workflow.pk).update(
        production_flag_galleys_ok=ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
    )

    SetPublisherConfirmedFlag(workflow=stale_workflow, confirmed=True).run()

    fresh = ArticleWorkflow.objects.get(pk=stale_workflow.pk)
    assert fresh.production_flag_iop_ready is True, "Flag must be set"
    assert fresh.production_flag_galleys_ok == ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED, "Galley status lost"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [
        ArticleWorkflow.ReviewStates.ACCEPTED,
        ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS,
        ArticleWorkflow.ReviewStates.PUBLISHED,
    ],
)
def test_set_rejected_in_other_states(assigned_to_typesetter_article: Article, settings, state):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, state)

    with pytest.raises(ValueError):
        SetPublisherConfirmedFlag(workflow=workflow, confirmed=True).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, f"Flag must not change in {state}"


@pytest.mark.django_db
def test_set_rejected_for_journal_not_requiring_confirmation(assigned_to_typesetter_article: Article, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    workflow = assigned_to_typesetter_article.articleworkflow

    with pytest.raises(ValueError):
        SetPublisherConfirmedFlag(workflow=workflow, confirmed=True).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Flag must not change for a journal outside the setting"


@pytest.mark.django_db
def test_view_eo_can_confirm(assigned_to_typesetter_article: Article, eo_user, client: Client, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    client.force_login(eo_user)

    response = client.post(_set_url(workflow, "1"))

    assert response.status_code == 302, "EO request must redirect"
    assert response.headers["Location"] == reverse("wjs_article_details", kwargs={"pk": workflow.pk})
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is True, "EO request must set the flag"


@pytest.mark.django_db
def test_view_stale_revoke_does_not_confirm(
    assigned_to_typesetter_article: Article, eo_user, client: Client, settings
):
    """A "Revoke" click from a stale page on an already-revoked paper must not open the gate."""
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    client.force_login(eo_user)

    response = client.post(_set_url(workflow, "0"))

    assert response.status_code == 302, "Refused request must redirect to the status page"
    assert [str(m) for m in get_messages(response.wsgi_request)], "Refused request must show an error message"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Stale revoke must leave the flag unset"


@pytest.mark.django_db
@pytest.mark.parametrize("querystring", ["", "?confirmed=", "?confirmed=yes"])
def test_view_rejects_missing_or_invalid_value(
    assigned_to_typesetter_article: Article, eo_user, client: Client, settings, querystring
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    client.force_login(eo_user)

    response = client.post(reverse("wjs_set_publisher_confirmed", kwargs={"pk": workflow.pk}) + querystring)

    assert response.status_code == 302, "Invalid request must redirect to the status page"
    assert [str(m) for m in get_messages(response.wsgi_request)], "Invalid request must show an error message"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Invalid request must not change the flag"


@pytest.mark.django_db
def test_view_forbidden_to_typesetter_and_author(
    assigned_to_typesetter_article: Article, typesetter, client: Client, settings
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow

    for user in (typesetter, assigned_to_typesetter_article.correspondence_author):
        client.force_login(user)
        response = client.post(_set_url(workflow, "1"))
        assert response.status_code == 403, f"{user} must not be allowed to set the flag"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Forbidden requests must not change the flag"


@pytest.mark.django_db
def test_view_error_in_wrong_state_shows_message(
    assigned_to_typesetter_article: Article, eo_user, client: Client, settings
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, ArticleWorkflow.ReviewStates.ACCEPTED)
    client.force_login(eo_user)

    response = client.post(_set_url(workflow, "1"))

    assert response.status_code == 302, "Rejected request must redirect to the status page"
    assert [str(m) for m in get_messages(response.wsgi_request)], "Rejected request must show an error message"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Rejected request must not change the flag"


@pytest.mark.django_db
def test_view_get_not_allowed(assigned_to_typesetter_article: Article, eo_user, client: Client, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    client.force_login(eo_user)

    response = client.get(_set_url(workflow, "1"))

    assert response.status_code == 405, "The view is POST-only"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "GET must not change the flag"


@pytest.mark.django_db
@pytest.mark.parametrize("state", TOGGLE_STATES)
def test_action_available_to_eo_in_production_states(
    assigned_to_typesetter_article: Article, eo_user, settings, state
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, state)
    assert PUBLISHER_ACTION_NAME in _available_action_names(workflow, eo_user), f"EO must see the action in {state}"


@pytest.mark.django_db
def test_action_not_available_to_typesetter(assigned_to_typesetter_article: Article, typesetter, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    assert PUBLISHER_ACTION_NAME not in _available_action_names(workflow, typesetter), "Typesetter must not see it"


@pytest.mark.django_db
def test_action_hidden_for_journal_not_requiring_confirmation(
    assigned_to_typesetter_article: Article, eo_user, settings
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    workflow = assigned_to_typesetter_article.articleworkflow
    assert PUBLISHER_ACTION_NAME not in _available_action_names(workflow, eo_user), "Hidden outside the setting"


@pytest.mark.django_db
def test_action_label_and_confirm_follow_flag(assigned_to_typesetter_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    action = states.BaseState.get_state_class(workflow).get_action_by_name(PUBLISHER_ACTION_NAME)

    as_dict = action.as_dict(workflow, eo_user)
    assert as_dict["label"] == "Publisher confirmed paper is publishable", "Label when flag is False"
    assert as_dict["url"] == _set_url(workflow, "1"), "Unconfirmed paper: the button confirms"
    assert as_dict["is_post"] is True, "Toggle must be a POST action"
    unset_confirm = as_dict["confirm"]

    workflow.production_flag_iop_ready = True
    as_dict = action.as_dict(workflow, eo_user)
    assert as_dict["label"] == "Revoke publisher confirmation", "Label when flag is True"
    assert as_dict["url"] == _set_url(workflow, "0"), "Confirmed paper: the button revokes"
    assert as_dict["confirm"] and as_dict["confirm"] != unset_confirm, "Confirm text must follow the flag"


def _begin_publication(rfp_article: Article, eo_user, fake_request: HttpRequest) -> BeginPublication:
    return BeginPublication(workflow=rfp_article.articleworkflow, user=eo_user, request=fake_request)


@pytest.mark.django_db
def test_begin_publication_blocked_without_confirmation(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (False, GATE_MESSAGE)


@pytest.mark.django_db
def test_begin_publication_allowed_with_confirmation(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    rfp_article.articleworkflow.production_flag_iop_ready = True
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (True, None)


@pytest.mark.django_db
def test_begin_publication_ignores_flag_for_other_journals(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    assert rfp_article.articleworkflow.production_flag_iop_ready is False
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (True, None)


@pytest.mark.django_db
def test_revoking_confirmation_blocks_publication_again(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    workflow.save()  # persist the RFP state set in memory by the fixture
    SetPublisherConfirmedFlag(workflow=workflow, confirmed=True).run()
    SetPublisherConfirmedFlag(workflow=workflow, confirmed=False).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (False, GATE_MESSAGE)


@pytest.mark.django_db
def test_begin_publication_view_redirects_with_message(rfp_article: Article, eo_user, client: Client, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    workflow.save()
    client.force_login(eo_user)

    with mock.patch("plugins.wjs_review.logic__production.FinishPublication.run") as finish:
        response = client.post(reverse("wjs_review_begin_publication", kwargs={"pk": workflow.pk}))

    assert response.status_code == 302, "Blocked publication must redirect"
    assert response.headers["Location"] == reverse("wjs_article_details", kwargs={"pk": workflow.pk})
    assert any(GATE_MESSAGE in str(m) for m in get_messages(response.wsgi_request)), "Gate message must be shown"
    finish.assert_not_called()
    workflow.refresh_from_db()
    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION, "State must not change"


@pytest.mark.django_db
def test_publish_disabled_until_confirmed(rfp_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    action = states.ReadyForPublication.get_action_by_name("begin publication")

    assert action.as_dict(workflow, eo_user)["disabled"] is True, "Publish disabled without confirmation"
    workflow.production_flag_iop_ready = True
    assert action.as_dict(workflow, eo_user)["disabled"] is False, "Publish enabled once confirmed"


@pytest.mark.django_db
def test_publish_never_disabled_for_other_journals(rfp_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    action = states.ReadyForPublication.get_action_by_name("begin publication")
    assert action.as_dict(rfp_article.articleworkflow, eo_user)["disabled"] is False, "Never disabled outside setting"


PUBLISH_DISABLED_CAUSE = (
    'Waiting for the publisher confirmation: use the "Publisher confirmed paper is publishable" action first.'
)


@pytest.mark.django_db
def test_disabled_publish_exposes_its_cause(rfp_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    action = states.ReadyForPublication.get_action_by_name("begin publication")
    assert action.as_dict(rfp_article.articleworkflow, eo_user)["disabled_cause"] == PUBLISH_DISABLED_CAUSE


@pytest.mark.django_db
def test_disabled_publish_renders_hoverable_cause(rfp_article: Article, eo_user, settings):
    """A disabled action has `pointer-events: none`: its cause is shown by a hoverable wrapper instead."""
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    action = states.ReadyForPublication.get_action_by_name("begin publication")

    html = render_to_string(
        "wjs_review/details/elements/actions_button.html", {"action": action.as_dict(workflow, eo_user)}
    )
    assert (
        '<span class="d-inline-block" tabindex="0" title="Waiting for the publisher confirmation' in html
    ), "Disabled Publish must be wrapped in a hoverable span carrying the cause"

    workflow.production_flag_iop_ready = True
    html = render_to_string(
        "wjs_review/details/elements/actions_button.html", {"action": action.as_dict(workflow, eo_user)}
    )
    assert 'class="d-inline-block"' not in html, "Enabled Publish must render as before, without the wrapper"


@pytest.mark.django_db
def test_disabled_action_without_cause_renders_as_before(assigned_to_typesetter_article: Article, typesetter):
    """Disabled actions that do not declare a cause keep today's markup."""
    workflow = assigned_to_typesetter_article.articleworkflow
    action = states.TypesetterSelected.get_action_by_name("Send for proofreading")
    action_dict = action.as_dict(workflow, typesetter)
    action_dict["disabled"] = True

    html = render_to_string("wjs_review/details/elements/actions_button.html", {"action": action_dict})
    assert 'class="d-inline-block"' not in html, "No wrapper without a disabled cause"
