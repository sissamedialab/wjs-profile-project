"""Late attention conditions must fire even when the watched (last) reminder was disabled.

A reminder disabled before being sent keeps ``date_sent = NULL`` forever: the late ACs for Editor / EO / Director
must then rely on its ``date_due``, with the same threshold they apply to ``date_sent`` (wjs/specs#2215).
"""

import datetime
from typing import Optional

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.http import HttpRequest
from django.utils import timezone
from plugins.wjs_review import ac_service, conditions
from plugins.wjs_review.forms import ToggleDisableRemindersForm
from plugins.wjs_review.logic import HandleDecision
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    EditorRevisionRequest,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .test_helpers import attention_conditions_rebuild


def _today() -> datetime.date:
    return timezone.localtime(timezone.now()).date()


def _set_reminder(
    target: Model,
    code: Reminder.ReminderCodes,
    *,
    date_due: datetime.date,
    disabled: bool = False,
    date_sent: Optional[datetime.datetime] = None,
) -> Reminder:
    """Set due date / disabled / sent date of the reminder ``code`` on ``target``, creating it if missing.

    A missing reminder is created by copying recipient, actor and message from another reminder of the same target.
    """
    content_type = ContentType.objects.get_for_model(target)
    target_reminders = Reminder.objects.filter(content_type=content_type, object_id=target.pk)
    reminder = target_reminders.filter(code=code).first()
    if reminder is None:
        template = target_reminders.first()
        assert template, f"No reminder to copy from on {target}"
        reminder = Reminder(
            code=code,
            content_type=content_type,
            object_id=target.pk,
            recipient=template.recipient,
            actor=template.actor,
            message_subject=template.message_subject,
            message_body=template.message_body,
        )
    reminder.date_due = date_due
    reminder.disabled = disabled
    reminder.date_sent = date_sent
    reminder.save()
    return reminder


# ---------------------------------------------------------------------------
# Editor-side conditions
# ---------------------------------------------------------------------------


def _editor_assignment(article: Article) -> WjsEditorAssignment:
    return WjsEditorAssignment.objects.get_current(article)


@pytest.mark.django_db
def test_editor_is_late_fires_for_disabled_last_reminder(assigned_article: Article):
    """A disabled EDMD3 due more than 3 days ago makes the editor late."""
    _set_reminder(
        _editor_assignment(assigned_article),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
        date_due=_today() - datetime.timedelta(days=4),
        disabled=True,
    )
    assert conditions.editor_is_late(assigned_article) == "Editor has not yet taken a decision"


@pytest.mark.django_db
def test_editor_is_late_ignores_disabled_reminder_within_threshold(assigned_article: Article):
    """A disabled EDMD3 due exactly 3 days ago is not yet past the threshold."""
    _set_reminder(
        _editor_assignment(assigned_article),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
        date_due=_today() - datetime.timedelta(days=3),
        disabled=True,
    )
    assert conditions.editor_is_late(assigned_article) == ""


@pytest.mark.django_db
def test_editor_is_late_ignores_enabled_unsent_overdue_reminder(assigned_article: Article):
    """An enabled, not-yet-sent EDMD3 does not count, however overdue."""
    _set_reminder(
        _editor_assignment(assigned_article),
        Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
        date_due=_today() - datetime.timedelta(days=10),
    )
    assert conditions.editor_is_late(assigned_article) == ""


@pytest.mark.django_db
def test_needs_assignment_eo_fires_for_disabled_last_reminder(assigned_article: Article):
    """A disabled EDSR3 due 5 days ago escalates the missing review assignment to EO/Director."""
    _set_reminder(
        _editor_assignment(assigned_article),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
        date_due=timezone.localtime(timezone.now() - datetime.timedelta(days=5)).date(),
        disabled=True,
    )
    assert (
        conditions.needs_assignment_all_editorreminders_sent(assigned_article)
        == "Review process not yet started/restarted"
    )


@pytest.mark.django_db
def test_needs_assignment_eo_ignores_disabled_reminder_within_threshold(assigned_article: Article):
    """A disabled EDSR3 due 4 days ago is not yet past the threshold."""
    _set_reminder(
        _editor_assignment(assigned_article),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
        date_due=_today() - datetime.timedelta(days=4),
        disabled=True,
    )
    assert conditions.needs_assignment_all_editorreminders_sent(assigned_article) == ""


@pytest.mark.django_db
def test_needs_assignment_eo_ignores_enabled_unsent_overdue_reminder(assigned_article: Article):
    """An enabled, not-yet-sent EDSR3 does not count, however overdue."""
    _set_reminder(
        _editor_assignment(assigned_article),
        Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3,
        date_due=_today() - datetime.timedelta(days=10),
    )
    assert conditions.needs_assignment_all_editorreminders_sent(assigned_article) == ""


# ---------------------------------------------------------------------------
# Reviewer conditions
# ---------------------------------------------------------------------------

REVIEWER_WATCHED = [
    (Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3, "Reviewer has not yet answered the invitation"),
    (Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2, "Reviewer is late"),
]


def _late_review_assignment(review_assignment: WorkflowReviewAssignment) -> WorkflowReviewAssignment:
    """Make the review assignment overdue (still pending)."""
    review_assignment.date_due = _today() - datetime.timedelta(days=1)
    review_assignment.save()
    return review_assignment


@pytest.mark.django_db
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_reviewer_is_late_editor_fires_for_disabled_last_reminder(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str
):
    """Editor view: a disabled last reminder due today counts as sent."""
    assignment = _late_review_assignment(review_assignment)
    _set_reminder(assignment, code, date_due=_today(), disabled=True)
    assert conditions.reviewer_is_late(assignment.article, for_editor=True) == message


@pytest.mark.django_db
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_reviewer_is_late_editor_ignores_disabled_future_reminder(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str
):
    """Editor view: a disabled last reminder due tomorrow does not count yet."""
    assignment = _late_review_assignment(review_assignment)
    _set_reminder(assignment, code, date_due=_today() + datetime.timedelta(days=1), disabled=True)
    assert conditions.reviewer_is_late(assignment.article, for_editor=True) == ""


@pytest.mark.django_db
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_reviewer_is_late_eo_fires_on_threshold_day(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str
):
    """EO/Director view: a disabled last reminder due exactly 5 days ago counts."""
    assignment = _late_review_assignment(review_assignment)
    due = timezone.localtime(timezone.now() - datetime.timedelta(days=5)).date()
    _set_reminder(assignment, code, date_due=due, disabled=True)
    assert conditions.reviewer_is_late(assignment.article) == message


@pytest.mark.django_db
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_reviewer_is_late_eo_ignores_day_after_threshold(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str
):
    """EO/Director view: a disabled last reminder due 4 days ago does not count yet."""
    assignment = _late_review_assignment(review_assignment)
    due = timezone.localtime(timezone.now() - datetime.timedelta(days=4)).date()
    _set_reminder(assignment, code, date_due=due, disabled=True)
    assert conditions.reviewer_is_late(assignment.article) == ""


@pytest.mark.django_db
@pytest.mark.parametrize("for_editor", [True, False])
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_reviewer_is_late_ignores_enabled_unsent_overdue_reminder(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str, for_editor: bool
):
    """An enabled, not-yet-sent last reminder does not count, however overdue."""
    assignment = _late_review_assignment(review_assignment)
    _set_reminder(assignment, code, date_due=_today() - datetime.timedelta(days=10))
    assert conditions.reviewer_is_late(assignment.article, for_editor=for_editor) == ""


@pytest.mark.django_db
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_any_reviewer_late_after_reminder_fires_for_disabled(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str
):
    """Editor "Reviewer does not respond": a disabled last reminder past WJS_REMINDER_LATE_AFTER counts."""
    assignment = _late_review_assignment(review_assignment)
    due = _today() - datetime.timedelta(days=settings.WJS_REMINDER_LATE_AFTER + 1)
    _set_reminder(assignment, code, date_due=due, disabled=True)
    assert (
        conditions.any_reviewer_is_late_after_reminder(assignment.article)
        == "Reviewer does not respond. Please take action"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("code,message", REVIEWER_WATCHED)
def test_any_reviewer_late_after_reminder_ignores_disabled_within_threshold(
    review_assignment: WorkflowReviewAssignment, code: Reminder.ReminderCodes, message: str
):
    """Editor "Reviewer does not respond": a disabled last reminder due WJS_REMINDER_LATE_AFTER days ago is not yet
    past the threshold."""
    assignment = _late_review_assignment(review_assignment)
    due = _today() - datetime.timedelta(days=settings.WJS_REMINDER_LATE_AFTER)
    _set_reminder(assignment, code, date_due=due, disabled=True)
    assert conditions.any_reviewer_is_late_after_reminder(assignment.article) == ""


# ---------------------------------------------------------------------------
# Author revision conditions (Editor: late_after_days=1, EO: late_after_days=2)
# ---------------------------------------------------------------------------

REVISION_DAYS_LATE = 5

AUTHOR_WATCHED = [
    pytest.param(
        ArticleWorkflow.Decisions.MAJOR_REVISION,
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2,
        conditions.author_revision_is_late_all_reminders_sent,
        f"Revision is {REVISION_DAYS_LATE} days late. Pls consider reminding author",
        id="major",
    ),
    pytest.param(
        ArticleWorkflow.Decisions.MINOR_REVISION,
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2,
        conditions.author_revision_is_late_all_reminders_sent,
        f"Revision is {REVISION_DAYS_LATE} days late. Pls consider reminding author",
        id="minor",
    ),
    pytest.param(
        ArticleWorkflow.Decisions.TECHNICAL_REVISION,
        Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2,
        conditions.author_technicalrevision_is_late_all_reminders_sent,
        "Author has not updated metadata",
        id="technical",
    ),
]


def _late_revision_request(article: Article, fake_request: HttpRequest, decision: str) -> EditorRevisionRequest:
    """Request a revision whose due date is REVISION_DAYS_LATE days in the past."""
    editor = WjsEditorAssignment.objects.get_current(article).editor
    return (
        HandleDecision(
            workflow=article.articleworkflow,
            form_data={
                "decision": decision,
                "decision_editor_report": "random message",
                "withdraw_notice": "notice",
                "date_due": timezone.localtime(timezone.now() - datetime.timedelta(days=REVISION_DAYS_LATE)),
            },
            user=editor,
            request=fake_request,
        )
        .run()
        .revision_request
    )


@pytest.mark.django_db
@pytest.mark.parametrize("late_after_days", [1, 2])
@pytest.mark.parametrize("decision,code,condition,message", AUTHOR_WATCHED)
def test_author_revision_late_fires_for_disabled_last_reminder(
    assigned_article: Article, fake_request: HttpRequest, decision, code, condition, message, late_after_days: int
):
    """A disabled last author reminder due more than late_after_days days ago counts as sent."""
    revision_request = _late_revision_request(assigned_article, fake_request, decision)
    _set_reminder(
        revision_request, code, date_due=_today() - datetime.timedelta(days=late_after_days + 1), disabled=True
    )
    assert condition(assigned_article, late_after_days=late_after_days) == message


@pytest.mark.django_db
@pytest.mark.parametrize("late_after_days", [1, 2])
@pytest.mark.parametrize("decision,code,condition,message", AUTHOR_WATCHED)
def test_author_revision_late_ignores_disabled_within_threshold(
    assigned_article: Article, fake_request: HttpRequest, decision, code, condition, message, late_after_days: int
):
    """A disabled last author reminder due exactly late_after_days days ago is not yet past the threshold."""
    revision_request = _late_revision_request(assigned_article, fake_request, decision)
    _set_reminder(revision_request, code, date_due=_today() - datetime.timedelta(days=late_after_days), disabled=True)
    assert condition(assigned_article, late_after_days=late_after_days) == ""


@pytest.mark.django_db
@pytest.mark.parametrize("decision,code,condition,message", AUTHOR_WATCHED)
def test_author_revision_late_ignores_enabled_unsent_overdue(
    assigned_article: Article, fake_request: HttpRequest, decision, code, condition, message
):
    """An enabled, not-yet-sent last author reminder does not count, however overdue."""
    revision_request = _late_revision_request(assigned_article, fake_request, decision)
    _set_reminder(revision_request, code, date_due=_today() - datetime.timedelta(days=10))
    assert condition(assigned_article, late_after_days=1) == ""


@pytest.mark.django_db
@pytest.mark.parametrize("decision,code,condition,message", AUTHOR_WATCHED)
def test_author_revision_late_ignores_disabled_on_completed_request(
    assigned_article: Article, fake_request: HttpRequest, decision, code, condition, message
):
    """A disabled overdue reminder on an already completed revision request does not fire."""
    revision_request = _late_revision_request(assigned_article, fake_request, decision)
    _set_reminder(revision_request, code, date_due=_today() - datetime.timedelta(days=10), disabled=True)
    revision_request.date_completed = timezone.now()
    revision_request.save()
    assert condition(assigned_article, late_after_days=1) == ""


# ---------------------------------------------------------------------------
# Toggling a reminder from the UI (ToggleDisableRemindersForm) re-evaluates time-based ACs
# ---------------------------------------------------------------------------


def _eo_reviewer_late_ac_exists(article: Article) -> bool:
    return AttentionCondition.objects.filter(
        article=article,
        user=get_eo_user(article),
        code=ac_service.REVIEWER_LATE_ESCALATED,
        status=AttentionCondition.Status.ACTIVE,
    ).exists()


def _toggle(reminder: Reminder) -> Reminder:
    form = ToggleDisableRemindersForm(data={}, instance=reminder)
    assert form.is_valid(), form.errors
    return form.save()


@pytest.fixture
def overdue_unsent_reea3(review_assignment: WorkflowReviewAssignment) -> Reminder:
    """An enabled, unsent REEA3 reminder due 10 days ago, on an overdue review assignment."""
    assignment = _late_review_assignment(review_assignment)
    return _set_reminder(
        assignment,
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
        date_due=_today() - datetime.timedelta(days=10),
    )


@pytest.mark.django_db
def test_toggle_disable_materialises_eo_ac(overdue_unsent_reea3: Reminder):
    """Disabling an overdue last reminder from the UI immediately raises the EO "reviewer late" AC."""
    article = overdue_unsent_reea3.get_related_article()
    attention_conditions_rebuild(article)
    assert not _eo_reviewer_late_ac_exists(article)

    reminder = _toggle(overdue_unsent_reea3)

    assert reminder.disabled
    assert _eo_reviewer_late_ac_exists(article)


@pytest.mark.django_db
def test_reenable_overdue_reminder_clears_ac(overdue_unsent_reea3: Reminder):
    """Re-enabling an overdue, still unsent reminder resolves the AC until the reminder is actually sent."""
    article = overdue_unsent_reea3.get_related_article()
    reminder = _toggle(overdue_unsent_reea3)
    assert _eo_reviewer_late_ac_exists(article)

    reminder = _toggle(reminder)

    assert not reminder.disabled
    assert conditions.reviewer_is_late(article) == ""
    assert not _eo_reviewer_late_ac_exists(article)
