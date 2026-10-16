"""Checks run on just-accepted articles: JCAP TA and imported papers (specs#2591, specs#3131)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from identifiers.models import Identifier
from plugins.wjs_review import ac_service
from plugins.wjs_review.events.checks_after_acceptance import (
    jcap_production_readiness_not_confirmed,
    render_production_readiness_subject,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    Message,
    WjsEditorAssignment,
)
from plugins.wjs_submission.models import AccessMode, ArticleSubmission
from plugins.wjs_submission.settings import OA_CODE_TA
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _accept_article

JCAP_CHECK = "plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed"


def _set_ta(article: Article) -> None:
    """Give the article the transformative-agreement access mode."""
    access_mode = AccessMode.objects.filter(code=OA_CODE_TA).first() or AccessMode.objects.create(
        name="Transformative agreement", code=OA_CODE_TA
    )
    submission_data = article.submission_data
    submission_data.access_mode = access_mode
    submission_data.save()


def _set_preprintid(article: Article, value: str = "JCAP_001P_1025") -> None:
    """Mark the article as imported from wjapp."""
    Identifier.objects.create(identifier=value, article=article, id_type="preprintid", enabled=True)


def _jcap_messages(article: Article):
    """Messages about this article sent by the JCAP check."""
    return Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.pk,
        subject=render_production_readiness_subject(article),
    )


@pytest.mark.django_db
def test_ta_paper_is_held(assigned_article):
    """A TA paper is held and the EO is notified (specs#2591)."""
    _set_ta(assigned_article)
    assert jcap_production_readiness_not_confirmed(assigned_article) is False, "TA paper must be held"
    messages = _jcap_messages(assigned_article)
    assert messages.count() == 1, "EO must receive one JCAP message"
    assert get_eo_user(assigned_article) in messages.first().recipients.all(), "The message must go to the EO"


@pytest.mark.django_db
def test_imported_paper_is_held(assigned_article):
    """An imported (non-TA) paper is held and the EO is notified (specs#3131)."""
    _set_preprintid(assigned_article)
    assert jcap_production_readiness_not_confirmed(assigned_article) is False, "Imported paper must be held"
    assert _jcap_messages(assigned_article).count() == 1, "EO must receive one JCAP message"


@pytest.mark.django_db
def test_imported_ta_paper_gets_one_message(assigned_article):
    """An imported TA paper is held with a single JCAP message: one condition, not two checks."""
    _set_ta(assigned_article)
    _set_preprintid(assigned_article)
    assert jcap_production_readiness_not_confirmed(assigned_article) is False, "Imported TA paper must be held"
    assert _jcap_messages(assigned_article).count() == 1, "Exactly one JCAP message expected"


@pytest.mark.django_db
def test_imported_paper_without_submission_data_is_held(assigned_article):
    """A missing ArticleSubmission row must not let an imported paper through."""
    _set_preprintid(assigned_article)
    ArticleSubmission.objects.filter(article=assigned_article).delete()
    article = Article.objects.get(pk=assigned_article.pk)
    assert jcap_production_readiness_not_confirmed(article) is False, "Imported paper without submission data held"


@pytest.mark.django_db
def test_native_non_ta_paper_passes(assigned_article):
    """A native (not imported) non-TA paper passes, without messages."""
    assert jcap_production_readiness_not_confirmed(assigned_article) is True, "Native non-TA paper must pass"
    assert not _jcap_messages(assigned_article).exists(), "No JCAP message expected"


@pytest.mark.django_db
def test_paper_without_submission_data_and_preprintid_passes(assigned_article):
    """No access mode information and no preprintid: the paper passes."""
    ArticleSubmission.objects.filter(article=assigned_article).delete()
    article = Article.objects.get(pk=assigned_article.pk)
    assert jcap_production_readiness_not_confirmed(article) is True, "Paper with no submission data must pass"


@pytest.mark.django_db
def test_empty_preprintid_is_not_imported(assigned_article):
    """An empty preprintid value is not a populated preprint ID: the paper passes."""
    _set_preprintid(assigned_article, value="")
    assert jcap_production_readiness_not_confirmed(assigned_article) is True, "Empty preprintid must not hold"


@pytest.mark.django_db
def test_check_configured_for_jcap_only(settings):
    """Only JCAP runs the check, under its new name: imported JCOM/JCOMAL papers are not held."""
    checks = settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS
    assert JCAP_CHECK in checks["JCAP"], "JCAP must run the check under its new dotted path"
    for journal_code in (None, "JCOM", "JCOMAL"):
        assert JCAP_CHECK not in checks[journal_code], f"{journal_code} must not run the JCAP check"


@pytest.mark.django_db
def test_accepting_imported_paper_holds_it_with_ac(assigned_article, fake_request: HttpRequest, settings):
    """End to end: accepting an imported paper leaves it in Accepted with the EO AC."""
    _set_preprintid(assigned_article)
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {assigned_article.journal.code: [JCAP_CHECK]}
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    _accept_article(fake_request, assigned_article)
    assigned_article.refresh_from_db()

    assert (
        assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED
    ), "Imported paper must stay in Accepted"
    assert (
        AttentionCondition.objects.active()
        .filter(article=assigned_article, user=get_eo_user(assigned_article), code=ac_service.ACCESS_MODE_TO_CHECK)
        .exists()
    ), "EO must get the 'Access mode to check' AC"
