"""Author-proofing-late attention condition (specs#3264).

As for the typesetter: the author is late only from the day after the due
date. ``GalleyProofing.due`` is a datetime: production stores the local
midnight of the due day (a date is assigned), read back from the DB in UTC.
"""

import datetime
from datetime import timedelta

import freezegun
import pytest
from django.utils import timezone
from plugins.wjs_review import ac_service, conditions
from plugins.wjs_review.models import AttentionCondition
from submission.models import Article
from typesetting.models import GalleyProofing

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _stage_proofing_article
from .test_helpers import attention_conditions_rebuild

DUE = datetime.date(2026, 10, 13)


def _local(day: datetime.date, hour: int = 12, minute: int = 0) -> datetime.datetime:
    """Return an aware datetime at the given local time of ``day``."""
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


def _stored_due(day: datetime.date) -> datetime.datetime:
    """Return ``due`` as production stores it and reads it back: local midnight, in UTC."""
    return _local(day, hour=0).astimezone(datetime.timezone.utc)


def _late_message(days: str) -> str:
    return f"Proofing is late by {days}. Was expected by 2026-10-13. Please contact the author."


@pytest.mark.parametrize(
    "now,expected",
    (
        (_local(DUE), ""),
        (_local(DUE, hour=23, minute=59), ""),
        (_local(DUE + timedelta(days=1), hour=0, minute=30), _late_message("1 day")),
        (_local(DUE + timedelta(days=3)), _late_message("3 days")),
    ),
)
def test_is_author_proofing_late(now, expected):
    """The author is late only from the day after the due date; the expected date is the local one."""
    assignment = GalleyProofing(due=_stored_due(DUE))
    with freezegun.freeze_time(now):
        assert conditions.is_author_proofing_late(assignment) == expected


def test_is_author_proofing_late_without_due():
    """No assignment or no due date: never late."""
    assert conditions.is_author_proofing_late(None) == ""
    assert conditions.is_author_proofing_late(GalleyProofing(due=None)) == ""


def _author_proofing_late_acs(article: Article):
    return AttentionCondition.objects.active().filter(article=article, code=ac_service.AUTHOR_PROOFING_LATE)


@pytest.mark.django_db
def test_evaluator_author_proofing_late_starts_day_after_due(stage_proofing_article):
    """The evaluator creates no AC on the due date, and the EO's AC the day after."""
    article = stage_proofing_article
    assignment = GalleyProofing.objects.get(round__article=article, proofreader=article.correspondence_author)
    assignment.due = DUE  # a date, as RequestProofs assigns it
    assignment.save()

    with freezegun.freeze_time(_local(DUE)):
        attention_conditions_rebuild(article)
    assert not _author_proofing_late_acs(article).exists()

    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _author_proofing_late_acs(article).get(user=get_eo_user(article)).message == _late_message("1 day")


@pytest.mark.django_db
def test_evaluator_resolves_author_proofing_late_when_no_longer_late(stage_proofing_article):
    """An existing AC is resolved when re-evaluated on the due date (the old rule said "late by 0 days")."""
    article = stage_proofing_article
    assignment = GalleyProofing.objects.get(round__article=article, proofreader=article.correspondence_author)
    assignment.due = DUE
    assignment.save()
    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _author_proofing_late_acs(article).exists()

    assignment.due = DUE + timedelta(days=1)
    assignment.save()
    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert not _author_proofing_late_acs(article).exists()


@pytest.mark.django_db
def test_request_proofs_due_date_counts_from_the_local_date(
    assigned_to_typesetter_article_with_files_to_typeset, typesetter, fake_request, settings
):
    """Just after local midnight (UTC still on the previous day) the due date counts from the local today."""
    local_today = datetime.date(2026, 10, 14)
    with freezegun.freeze_time(_local(local_today, hour=0, minute=30)):
        assert timezone.now().date() == local_today - timedelta(days=1)  # precondition: UTC is still on the 13th
        article = _stage_proofing_article(
            assigned_to_typesetter_article_with_files_to_typeset, typesetter, fake_request
        )
    assignment = GalleyProofing.objects.get(round__article=article, proofreader=article.correspondence_author)
    expected = local_today + timedelta(days=settings.PROOFING_ASSIGNMENT_MAX_DUE_DAYS)
    assert timezone.localtime(assignment.due).date() == expected
