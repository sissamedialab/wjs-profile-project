"""Typesetter-late attention condition (specs#3264).

The AC must start on *last action + 4 days*: the assignment's ``due`` is
*last action + TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS* (3), so the typesetter
is late only from the day after ``due``.
"""

import datetime
from datetime import timedelta

import freezegun
import pytest
from django.utils import timezone
from plugins.wjs_review import ac_service, conditions
from plugins.wjs_review.models import AttentionCondition
from submission.models import Article
from typesetting.models import TypesettingAssignment

from wjs.jcom_profile.utils import get_eo_user

from .test_helpers import attention_conditions_rebuild

DUE = datetime.date(2026, 10, 13)
"""Due date of an assignment made on 10.10: the AC must start on 14.10."""


def _local(day: datetime.date, hour: int = 12, minute: int = 0) -> datetime.datetime:
    """Return an aware datetime at the given local time of ``day``."""
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


@pytest.mark.parametrize(
    "today,expected_eo,expected_typesetter",
    (
        (DUE - timedelta(days=1), "", ""),
        (DUE, "", ""),
        (DUE + timedelta(days=1), "Typesetter is 1 day late", "You are 1 day late"),
        (DUE + timedelta(days=3), "Typesetter is 3 days late", "You are 3 days late"),
    ),
)
def test_is_typesetter_late(today, expected_eo, expected_typesetter):
    """The typesetter is late only from the day after the due date."""
    assignment = TypesettingAssignment(due=DUE)
    with freezegun.freeze_time(_local(today)):
        assert conditions.is_typesetter_late(assignment) == expected_eo
        assert conditions.is_typesetter_late(assignment, for_typesetter=True) == expected_typesetter


def test_is_typesetter_late_without_due():
    """No assignment or no due date: never late."""
    assert conditions.is_typesetter_late(None) == ""
    assert conditions.is_typesetter_late(TypesettingAssignment(due=None)) == ""


def test_is_typesetter_late_uses_local_date(settings):
    """Just after local midnight of due + 1 the typesetter is late, even if the UTC date is still the due date."""
    settings.TIME_ZONE = "Europe/Rome"
    assignment = TypesettingAssignment(due=DUE)
    with freezegun.freeze_time(_local(DUE + timedelta(days=1), hour=0, minute=30)):
        assert timezone.now().date() == DUE  # precondition: UTC is still on the due date
        assert conditions.is_typesetter_late(assignment) == "Typesetter is 1 day late"


def _typesetter_late_acs(article: Article):
    return AttentionCondition.objects.active().filter(article=article, code=ac_service.TYPESETTER_LATE)


def _set_due(article: Article, due: datetime.date) -> None:
    assignment = TypesettingAssignment.objects.get(round__article=article)
    assignment.due = due
    assignment.save()


@pytest.mark.django_db
def test_evaluator_typesetter_late_starts_day_after_due(assigned_to_typesetter_article, typesetter):
    """The evaluator creates no AC on the due date, and the AC for typesetter and EO the day after."""
    article = assigned_to_typesetter_article
    _set_due(article, DUE)

    with freezegun.freeze_time(_local(DUE)):
        attention_conditions_rebuild(article)
    assert not _typesetter_late_acs(article).exists()

    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _typesetter_late_acs(article).get(user=typesetter).message == "You are 1 day late"
    assert _typesetter_late_acs(article).get(user=get_eo_user(article)).message == "Typesetter is 1 day late"


@pytest.mark.django_db
def test_evaluator_resolves_typesetter_late_when_no_longer_late(assigned_to_typesetter_article):
    """An existing AC (e.g. a "0 days late" row from the old rule) is resolved when re-evaluated on the due date."""
    article = assigned_to_typesetter_article
    _set_due(article, DUE)
    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert _typesetter_late_acs(article).exists()

    _set_due(article, DUE + timedelta(days=1))
    with freezegun.freeze_time(_local(DUE + timedelta(days=1))):
        attention_conditions_rebuild(article)
    assert not _typesetter_late_acs(article).exists()
