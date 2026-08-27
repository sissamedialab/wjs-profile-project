"""Test (some) attention conditions."""

import datetime
from datetime import timedelta
from typing import Callable

import freezegun
import pytest
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.http import HttpRequest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime, now
from journal.models import Journal
from plugins.wjs_review import ac_service, communication_utils, conditions, states
from plugins.wjs_review.ac_service import ACStateEvaluator
from plugins.wjs_review.forms import ToggleMessageReadByEOForm, ToggleMessageReadForm
from plugins.wjs_review.logic import (
    AssignToReviewer,
    AuthorHandleRevisionObsolete,
    EvaluateReview,
    HandleDecision,
    HandleEditorDeclinesAssignment,
    PostponeReviewerDueDate,
)
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    EditorRevisionRequest,
    Message,
    MessageRecipients,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from plugins.wjs_review.states import EditorSelected, EditorToBeSelected
from review import models as review_models
from review.models import ReviewForm
from submission.models import Article
from typesetting.models import GalleyProofing

from wjs.jcom_profile import constants
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import get_eo_user

from .test_helpers import (
    attention_condition_ignoring_unread,
    attention_conditions_rebuild,
)


@pytest.mark.skipif("not config.getoption('--run-academic')")
@pytest.mark.django_db
def test_multiple_updates(assigned_article):
    """Test that multiple updates on a queryset work as expected."""
    article = assigned_article  # alias
    original_title = article.title
    assert original_title != "AAA"
    assert original_title != "BBB"

    aqs = Article.objects.filter(id=article.pk)

    aqs.update(title="AAA")
    article.refresh_from_db()
    assert article.title == "AAA"

    aqs.update(title="BBB")
    article.refresh_from_db()
    assert article.title == "BBB"

    # But check that your queryset is not built upon something that you are going to change!
    # E.g.:
    aqs = Article.objects.filter(id=article.pk, title="BBB")

    aqs.update(title="CCC")
    article.refresh_from_db()
    assert article.title == "CCC"

    # now you have changed the title, so they _query_ of the queryset won't return anything
    updated = aqs.update(title="DDD")
    assert updated == 0
    article.refresh_from_db()
    assert article.title == "CCC"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "decision",
    (
        (ArticleWorkflow.Decisions.MINOR_REVISION),
        (ArticleWorkflow.Decisions.MAJOR_REVISION),
    ),
)
def test_author_revision_is_late(
    assigned_article: Article,
    fake_request: HttpRequest,
    decision: ArticleWorkflow.Decisions,
):
    """Test attention conditions when author revision is late.

    Author: revision request is past due date
    Editor: revision request is past due date _and_ last reminder has been sent yesterday
    EO: revision request is past due date _and_ last reminder has been sent two days ago
    """
    # just some alias
    article = assigned_article
    workflow = article.articleworkflow

    # sanity check: starting clean (no revision requests)
    assert not EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_completed__isnull=True,
        type=decision,
    ).exists()

    author = article.correspondence_author
    section_editor = WjsEditorAssignment.objects.get_current(article).editor
    eo = get_eo_user(article)

    days_past = 5
    expected = localtime(now() + timezone.timedelta(days=-days_past))  # note the "-": the author is late!
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": expected,
    }
    editor_decision = HandleDecision(
        workflow=workflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    ).run()
    revision_request = editor_decision.revision_request
    article.refresh_from_db()
    workflow.refresh_from_db()

    assert workflow.state == ArticleWorkflow.ReviewStates.TO_BE_REVISED

    # sanity check: we have unsent reminders
    all_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(revision_request),
        object_id=revision_request.id,
        disabled=False,
    )
    reminders = all_reminders.filter(
        date_sent__isnull=True,
    )
    assert reminders.exists()
    assert all_reminders.count() == reminders.count()
    reminders_count = reminders.count()

    expected = localtime(expected).date()

    # author has a.c., but editor and eo don't, because reminders are not yet sent
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent today, same a before
    updated = reminders.update(date_sent=now())
    assert updated == reminders_count
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent yesterday, same as before
    # NB: using `all_reminders` because we set the date_sent above
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(1))
    assert updated == reminders_count
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent more than 1 day ago, also editor has a.c.
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(2))
    assert updated == reminders_count
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert (
        attention_condition_ignoring_unread(article=article, user=section_editor)
        == f"Revision is {days_past} days late. Pls consider reminding author"
    )
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent more than two days ago, even EO has a.c.
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(3))
    assert updated == reminders_count
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert (
        attention_condition_ignoring_unread(article=article, user=section_editor)
        == f"Revision is {days_past} days late. Pls consider reminding author"
    )
    assert (
        attention_condition_ignoring_unread(article=article, user=eo)
        == f"Revision is {days_past} days late. Pls consider reminding author"
    )


@pytest.mark.django_db
def test_author_technicalrevision_is_late(
    assigned_article: Article,
    fake_request: HttpRequest,
):
    """Test attention conditions when author _technical_ revision is late.

    Author: revision request is past due date
    Editor: revision request is past due date _and_ last reminder has been sent yesterday
    EO: revision request is past due date _and_ last reminder has been sent two days ago
    """
    decision = ArticleWorkflow.Decisions.TECHNICAL_REVISION

    # just some alias
    article = assigned_article
    workflow = article.articleworkflow

    # sanity check: starting clean (no revision requests)
    assert not EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_completed__isnull=True,
        type=decision,
    ).exists()

    author = article.correspondence_author
    section_editor = WjsEditorAssignment.objects.get_current(article).editor
    eo = get_eo_user(article)

    days_past = 5
    expected = localtime(now() + timezone.timedelta(days=-days_past))  # note the "-": the author is late!
    form_data = {
        "decision": decision,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": expected,
    }
    editor_decision = HandleDecision(
        workflow=workflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    ).run()
    revision_request = editor_decision.revision_request
    article.refresh_from_db()
    workflow.refresh_from_db()

    assert workflow.state == ArticleWorkflow.ReviewStates.TO_BE_REVISED

    # sanity check: we have unsent reminders
    all_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(revision_request),
        object_id=revision_request.id,
        disabled=False,
    )
    reminders = all_reminders.filter(
        date_sent__isnull=True,
    )
    assert reminders.exists()
    assert all_reminders.count() == reminders.count()

    expected = expected.date()

    # author has a.c., but editor and eo don't, because reminders are not yet sent
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent today, same a before
    reminders.update(date_sent=now())
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent yesterday, same as before
    all_reminders.update(date_sent=now() - timezone.timedelta(1))
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent more than 1 day ago, also editor has a.c.
    all_reminders.update(date_sent=now() - timezone.timedelta(2))
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert (
        attention_condition_ignoring_unread(article=article, user=section_editor) == "Author has not updated metadata"
    )
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    # all reminders sent more than two days ago, even EO has a.c.
    all_reminders.update(date_sent=now() - timezone.timedelta(3))
    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert (
        attention_condition_ignoring_unread(article=article, user=section_editor) == "Author has not updated metadata"
    )
    assert attention_condition_ignoring_unread(article=article, user=eo) == "Author has not updated metadata"


@pytest.mark.django_db
def test_author_technicalrevision_is_late_with_revision(
    editor_revision: EditorRevisionRequest,
    fake_request: HttpRequest,
):
    """
    Test attention conditions when author _technical_ revision is late after a major revision.

    See specs#1466.
    """
    # just some alias
    article = editor_revision.article
    workflow = article.articleworkflow
    author = article.correspondence_author
    section_editor = WjsEditorAssignment.objects.get_current(article).editor

    AuthorHandleRevisionObsolete(
        revision=editor_revision,
        form_data={"author_noe": "any"},
        user=author,
        request=fake_request,
    ).run()

    days_past = 5
    expected = localtime(now() + timezone.timedelta(days=-days_past))  # note the "-": the author is late!
    form_data = {
        "decision": ArticleWorkflow.Decisions.TECHNICAL_REVISION,
        "decision_editor_report": "random message",
        "withdraw_notice": "notice",
        "date_due": expected,
    }
    HandleDecision(
        workflow=workflow,
        form_data=form_data,
        user=section_editor,
        request=fake_request,
    ).run()

    # This is the pesky condition that we are testing
    conditions.author_revision_is_late_all_reminders_sent(
        article,
        late_after_days=1,
    )
    # This is here as reminder/documentation
    conditions.author_technicalrevision_is_late_all_reminders_sent(
        article,
        late_after_days=1,
    )


@pytest.mark.django_db
def test_author_appeal_is_late(
    under_appeal_article: Article,
    fake_request: HttpRequest,
):
    """Test attention conditions when author is late in submitting an appeal."""
    # just some alias
    article = under_appeal_article
    workflow = article.articleworkflow

    assert workflow.state == ArticleWorkflow.ReviewStates.UNDER_APPEAL

    # get the open-appeal revision request
    openappeal_err = EditorRevisionRequest.objects.get(
        article_id=article.id,
        date_completed__isnull=True,
        type=ArticleWorkflow.Decisions.OPEN_APPEAL,
    )

    author = article.correspondence_author
    eo = get_eo_user(article)
    section_editor = openappeal_err.editor
    assert section_editor == eo

    days_past = 5
    expected = now() + timezone.timedelta(days=-days_past)  # note the "-": the author is late!
    openappeal_err.date_due = expected
    openappeal_err.save()
    attention_conditions_rebuild(article)

    state_cls = getattr(states, workflow.state)
    expected = expected.date()

    # author's attention condition is always active in this state and reports a simple message
    assert state_cls.article_requires_attention(article=article, user=author) == "Appeal to submit"
    # if EO visits "my editor pages" he sees the paper under appeal with an attention condition
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == f"Appeal is {days_past} days late. Withdraw?"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=eo) == f"Appeal is {days_past} days late. Withdraw?"
    )


@pytest.mark.django_db
def test_author_proofing_is_late(
    stage_proofing_article: Article,
    fake_request: HttpRequest,
):
    """Test attention conditions when author is late in submitting proofs (during production)."""
    # just some alias
    article = stage_proofing_article
    workflow = article.articleworkflow

    assert workflow.state == ArticleWorkflow.ReviewStates.PROOFREADING

    # get the proofing request (easy because the fixture generates only one)
    assignment = GalleyProofing.objects.get(
        round__article=article,
        proofreader=article.correspondence_author,
    )

    author = article.correspondence_author
    eo = get_eo_user(article)
    typesetter = assignment.round.typesettingassignment.typesetter

    days_past = 5
    expected = now() + timezone.timedelta(days=-days_past)  # note the "-": the author is late!
    assignment.due = expected
    assignment.save()
    attention_conditions_rebuild(article)

    state_cls = getattr(states, workflow.state)
    expected = expected.date()

    assert state_cls.article_requires_attention(article=article, user=author) == ""
    assert state_cls.article_requires_attention(article=article, user=typesetter) == ""
    assert (
        state_cls.article_requires_attention(article=article, user=eo)
        == f"Proofing is late by {(timezone.now() - assignment.due).days} days."
        f" Was expected by {assignment.due.strftime('%F')}."
        " Please contact the author."
    )


@pytest.mark.django_db
def test_needs_assignment(assigned_article: Article, director: JCOMProfile):
    """Test that the editor and director have an a.c. for papers that need review assignments."""
    # just some alias
    article = assigned_article
    workflow = article.articleworkflow

    author = article.correspondence_author
    eo = get_eo_user(article)
    editor_assignment = WjsEditorAssignment.objects.get_current(article)
    section_editor = editor_assignment.editor

    # sanity check: state, no review assignments and reminders
    assert workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert not WorkflowReviewAssignment.objects.filter(article=article).exists()
    all_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(editor_assignment),
        object_id=editor_assignment.id,
        disabled=False,
    )
    reminders = all_reminders.filter(
        date_sent__isnull=True,
    )
    assert reminders.exists()
    assert all_reminders.count() == reminders.count()

    state_cls = getattr(states, workflow.state)

    # editor has a.c. but director doesn't, because reminders are not yet sent
    attention_conditions_rebuild(article)
    assert state_cls.article_requires_attention(article=article, user=author) == ""
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == "Review process should start/restart"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == ""
    assert state_cls.article_requires_attention(article=article, user=director) == ""

    # all reminders sent today, a.c. for editor
    reminders_list = list(reminders)
    for reminder in reminders_list:
        reminder.date_sent = timezone.now()
        reminder.save()
    attention_conditions_rebuild(article)

    assert state_cls.article_requires_attention(article=article, user=author) == ""
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == "Review process should start/restart"
    )

    # all reminders sent 5 days ago, a.c. for director and EO
    for reminder in reminders_list:
        reminder.date_sent = timezone.now() - datetime.timedelta(days=5)
        reminder.save()
    attention_conditions_rebuild(article)

    assert state_cls.article_requires_attention(article=article, user=eo) == "Review process not yet started/restarted"
    assert (
        state_cls.article_requires_attention(article=article, user=director)
        == "Review process not yet started/restarted"
    )


@pytest.mark.django_db
def test_reviewer_is_late(
    assigned_article: Article,
    reviewer: JCOMProfile,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
):
    """Test a.c. for reviewer wrt accept/decline and report."""
    # just some alias
    article = assigned_article
    workflow = article.articleworkflow
    eo = get_eo_user(article)

    editor_assignment = WjsEditorAssignment.objects.get_current(article)
    section_editor = editor_assignment.editor

    assert not WorkflowReviewAssignment.objects.filter(article=article, reviewer=reviewer).exists()

    fake_request.user = section_editor
    assignment = AssignToReviewer(
        workflow=workflow,
        reviewer=reviewer.janeway_account,
        editor=section_editor,
        form_data={
            "acceptance_due_date": (localtime(timezone.now() + timezone.timedelta(1))).strftime("%Y-%m-%d"),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()

    assert assignment.date_accepted is None
    assert assignment.date_due > timezone.now().date()

    attention_conditions_rebuild(article)
    assert attention_condition_ignoring_unread(article=article, user=reviewer) == ""

    # accept/decline overdue
    assignment.date_due = localtime(timezone.now()).date() - timezone.timedelta(days=1)
    assignment.save()
    attention_conditions_rebuild(article)
    assert attention_condition_ignoring_unread(article=article, user=reviewer) == "Invite to be accepted/declined"

    all_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
        disabled=False,
    )
    reminders = all_reminders.filter(
        date_sent__isnull=True,
    )
    assert reminders.exists()
    assert all_reminders.count() == reminders.count()

    reminders_list = list(reminders)
    for reminder in reminders_list:
        reminder.date_sent = localtime(timezone.now()).date() - timezone.timedelta(days=3)
        reminder.save()

    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=section_editor)
        == "Reviewer has not yet answered the invitation"
    )
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""

    for reminder in reminders_list:
        reminder.date_sent = timezone.now() - datetime.timedelta(days=10)
        reminder.save()

    attention_conditions_rebuild(article)
    assert (
        attention_condition_ignoring_unread(article=article, user=eo) == "Reviewer has not yet answered the invitation"
    )
    # report overdue
    EvaluateReview(
        assignment=assignment,
        reviewer=reviewer.janeway_account,
        editor=section_editor,
        form_data={
            "reviewer_decision": "1",  # "1" means "accept"
            "additional_comments": "Additional comments",
            "date_due": localtime(timezone.now()).date() - timezone.timedelta(days=1),
        },
        request=fake_request,
        token="",
    ).run()
    attention_conditions_rebuild(article)
    assert attention_condition_ignoring_unread(article=article, user=reviewer) == "Review is overdue"

    reminders = all_reminders.filter(
        date_sent__isnull=True,
    )
    assert reminders.exists()
    assert all_reminders.count() == reminders.count()

    reminders_list = list(reminders)
    for reminder in reminders_list:
        reminder.date_sent = localtime(timezone.now()).date() - timezone.timedelta(days=3)
        reminder.save()

    attention_conditions_rebuild(article)
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == "Reviewer is late"
    # EO has no reviewer-related a.c. yet, but they do have unread messages, because the message
    # "reviewer accepted invite" from reviewer to editor is not marked "read-by-eo"
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""
    assert conditions.has_unread_message(article, recipient=eo) == "You have unread messages"

    for reminder in reminders_list:
        reminder.date_sent = timezone.now() - datetime.timedelta(days=10)
        reminder.save()

    attention_conditions_rebuild(article)
    assert attention_condition_ignoring_unread(article=article, user=eo) == "Reviewer is late"

    form_data = {
        "date_due": assignment.date_due + datetime.timedelta(days=3),
    }
    service = PostponeReviewerDueDate(
        assignment=assignment,
        editor=assignment.editor,
        user=assignment.editor,
        form_data=form_data,
        request=fake_request,
        original_due_date=assignment.date_due,
    )
    service.run()
    Message.objects.update(read_by_eo=True)
    MessageRecipients.objects.update(read=True)
    attention_conditions_rebuild(article)
    assert attention_condition_ignoring_unread(article=article, user=eo) == ""
    assert attention_condition_ignoring_unread(article=article, user=section_editor) == ""


@pytest.mark.django_db
def test_editor_assignment_after_deassignment_is_late(
    assigned_article: Article,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
):
    """
    Attention condition message when a past editor exists starts from the end of the previous editor assignment.
    """
    assigned_article.date_submitted = now() - timedelta(days=10)
    assigned_article.save()
    editor_assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    section_editor = editor_assignment.editor

    with freezegun.freeze_time(now() - timedelta(days=5)):
        form_data = {"decline_reason": "something"}
        service = HandleEditorDeclinesAssignment(
            assignment=editor_assignment,
            editor=section_editor,
            form_data=form_data,
            request=fake_request,
        )
        service.run()

    attention_conditions_rebuild(assigned_article)
    message = EditorToBeSelected.article_requires_eo_attention(assigned_article)
    assert "5 days" in message


@pytest.mark.django_db
def test_editor_is_late(
    assigned_article: Article,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    eo_user: JCOMProfile,
):
    """
    Attention condition message when editor has not selected a reviewer.
    """
    assigned_article.date_submitted = now() - timedelta(days=20)
    assigned_article.save()
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)

    all_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
        disabled=False,
    )
    for reminder in all_reminders:
        reminder.date_sent = timezone.now() - datetime.timedelta(days=5)
        reminder.save()

    attention_conditions_rebuild(assigned_article)
    message = EditorSelected.article_requires_eo_attention(assigned_article, eo_user)
    assert message == "Review process not yet started/restarted"


@pytest.mark.django_db
def test_editor_first_assignment_is_late(
    submitted_article: Article,
    review_form: review_models.ReviewForm,
    eo_user: JCOMProfile,
):
    """
    Attention condition message when no (present or past) editor exists starts from the submission date.
    """
    submitted_article.date_submitted = now() - timedelta(days=10)
    submitted_article.save()
    submitted_article.articleworkflow.state = ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
    submitted_article.articleworkflow.save()

    attention_conditions_rebuild(submitted_article)
    message = EditorToBeSelected.article_requires_eo_attention(submitted_article)
    assert "10 days" in message


# ---------------------------------------------------------------------------
# HAS_UNREAD_MESSAGE: the materialized AC of unread messages
# ---------------------------------------------------------------------------


def _unread_message_acs(article: Article, user: JCOMProfile):
    """Return the HAS_UNREAD_MESSAGE attention conditions of a user for an article (any status)."""
    return AttentionCondition.objects.filter(
        article=article,
        user=user,
        code=ac_service.HAS_UNREAD_MESSAGE,
    )


def _send_message(article: Article, actor: JCOMProfile, recipient: JCOMProfile) -> Message:
    """Create a message from actor to recipient, the way the business logic does."""
    return communication_utils.log_operation(
        article=article,
        message_subject="Subject",
        message_body="Body",
        actor=actor,
        recipients=[recipient],
    )


def _flag_message(message: Message, recipient: JCOMProfile, *, read: bool) -> None:
    """Flag a message as read/unread for one recipient, the way the toggle view does."""
    instance = MessageRecipients.objects.get(message=message, recipient=recipient)
    form = ToggleMessageReadForm(data={"read": "on"} if read else {}, instance=instance)
    assert form.is_valid(), f"Unexpected form errors: {form.errors}"
    form.save()


@pytest.mark.django_db
def test_new_message_creates_unread_message_ac(assigned_article: Article, normal_user: JCOMProfile):
    """The recipient of a new message gets one HAS_UNREAD_MESSAGE AC (and one only)."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    actor = normal_user.janeway_account
    # Drop the fixture's own messages/ACs, so that we look only at what we create here
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    _send_message(article, actor, editor)

    acs = _unread_message_acs(article, editor)
    assert acs.count() == 1, "The recipient of a new message should have one HAS_UNREAD_MESSAGE AC"
    assert acs.get().status == AttentionCondition.Status.ACTIVE, "The AC of a new message should be active"
    assert not _unread_message_acs(article, actor).exists(), "The actor of a message has nothing to read"

    # A second message does not add a second AC: one per (article, user) is enough
    _send_message(article, actor, editor)
    assert _unread_message_acs(article, editor).count() == 1, "A second message should not create a second AC"
    assert _unread_message_acs(article, editor).get().status == AttentionCondition.Status.ACTIVE


@pytest.mark.django_db
def test_unread_message_ac_is_resolved_only_when_nothing_is_left_unread(
    assigned_article: Article,
    normal_user: JCOMProfile,
):
    """The AC is resolved when the recipient has no other unread message, and comes back if flagged unread."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    actor = normal_user.janeway_account
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    m1 = _send_message(article, actor, editor)
    m2 = _send_message(article, actor, editor)
    assert _unread_message_acs(article, editor).get().status == AttentionCondition.Status.ACTIVE

    _flag_message(m1, editor, read=True)
    assert (
        _unread_message_acs(article, editor).get().status == AttentionCondition.Status.ACTIVE
    ), "The AC should survive: one message is still unread"

    _flag_message(m2, editor, read=True)
    assert (
        _unread_message_acs(article, editor).get().status == AttentionCondition.Status.RESOLVED
    ), "The AC should be resolved: no message is left unread"

    _flag_message(m2, editor, read=False)
    assert (
        _unread_message_acs(article, editor).get().status == AttentionCondition.Status.ACTIVE
    ), "The AC should come back when a message is flagged as unread again"


def _create_eo_human(create_jcom_user: Callable, eo_group: Group, journal: Journal) -> JCOMProfile:
    """Create an EO person: a member of the EO group, enrolled in the journal."""
    eo_human = create_jcom_user("eo_human")
    eo_human.groups.add(eo_group)
    eo_human.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    return eo_human


def _displayed_attention_condition(article: Article, user: JCOMProfile) -> str:
    """Return the AC that the given user sees for the article, through the display path."""
    return getattr(states, article.articleworkflow.state).article_requires_attention(article=article, user=user)


def _flag_message_read_by_eo(message: Message, *, read: bool) -> None:
    """Flag a message as read/unread by EO, the way the toggle view does."""
    form = ToggleMessageReadByEOForm(data={"read_by_eo": "on"} if read else {}, instance=message)
    assert form.is_valid(), f"Unexpected form errors: {form.errors}"
    form.save()


@pytest.mark.django_db
def test_new_message_creates_unread_message_ac_for_eo(
    assigned_article: Article,
    eo_user: JCOMProfile,
    normal_user: JCOMProfile,
    create_jcom_user: Callable,
    eo_group: Group,
    journal: Journal,
):
    """The editorial office gets one AC, on the EO system user, and every EO person sees it."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    eo_system_user = eo_user.janeway_account
    eo_human = _create_eo_human(create_jcom_user, eo_group, journal)
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    message = communication_utils.log_operation(
        article=article,
        message_subject="Subject",
        message_body="Body",
        actor=normal_user.janeway_account,
        recipients=[editor],
        flag_as_read_by_eo=False,
    )
    assert not message.read_by_eo

    # One AC only, on the EO system user: it belongs to the office, not to its members
    acs = _unread_message_acs(article, eo_system_user)
    assert acs.count() == 1, "The EO system user should have one HAS_UNREAD_MESSAGE AC"
    assert acs.get().status == AttentionCondition.Status.ACTIVE, "The EO system user's AC should be active"
    assert not _unread_message_acs(article, eo_human.janeway_account).exists(), "EO people share the office's AC"

    # ... and every EO person sees it
    for eo in (eo_system_user, eo_human.janeway_account):
        assert (
            _displayed_attention_condition(article, eo) == "You have unread messages"
        ), f"{eo} should see the editorial office's AC"

    # Flagging the message as read-by-eo clears it for all of EO
    _flag_message_read_by_eo(message, read=True)
    assert (
        _unread_message_acs(article, eo_system_user).get().status == AttentionCondition.Status.RESOLVED
    ), "The office's AC should be resolved once the message is read by EO"
    for eo in (eo_system_user, eo_human.janeway_account):
        assert _displayed_attention_condition(article, eo) == "", f"{eo} should see no unread-messages AC any more"

    # ... and it comes back if the message is flagged as unread again
    _flag_message_read_by_eo(message, read=False)
    assert (
        _unread_message_acs(article, eo_system_user).get().status == AttentionCondition.Status.ACTIVE
    ), "The office's AC should come back when the message is flagged as unread by EO"
    for eo in (eo_system_user, eo_human.janeway_account):
        assert _displayed_attention_condition(article, eo) == "You have unread messages", f"{eo} should see it again"


@pytest.mark.django_db
def test_read_by_eo_clears_the_ac_of_an_eo_person_who_is_also_a_recipient(
    assigned_article: Article,
    eo_user: JCOMProfile,  # noqa: ARG001
    normal_user: JCOMProfile,
    create_jcom_user: Callable,
    eo_group: Group,
    journal: Journal,
):
    """An EO person reads through the office's AC: read-by-eo clears it even if their own row is unread."""
    article = assigned_article
    eo_human = _create_eo_human(create_jcom_user, eo_group, journal)
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    message = communication_utils.log_operation(
        article=article,
        message_subject="Subject",
        message_body="Body",
        actor=normal_user.janeway_account,
        recipients=[eo_human.janeway_account],
        flag_as_read_by_eo=False,
    )
    assert (
        MessageRecipients.objects.get(message=message, recipient=eo_human.janeway_account).read is False
    ), "The message is unread for this EO person"
    assert _displayed_attention_condition(article, eo_human.janeway_account) == "You have unread messages"
    assert not _unread_message_acs(article, eo_human.janeway_account).exists(), "EO people have no personal AC"

    _flag_message_read_by_eo(message, read=True)

    assert (
        MessageRecipients.objects.get(message=message, recipient=eo_human.janeway_account).read is False
    ), "Their own row is still unread..."
    assert (
        _displayed_attention_condition(article, eo_human.janeway_account) == ""
    ), "...but read-by-eo clears the AC for them too"


@pytest.mark.django_db
def test_populate_resolves_a_personal_unread_message_ac_of_an_eo_person(
    assigned_article: Article,
    eo_user: JCOMProfile,  # noqa: ARG001
    normal_user: JCOMProfile,
    create_jcom_user: Callable,
    eo_group: Group,
    journal: Journal,
):
    """EO people share the office's AC: a personal leftover of theirs is resolved by the populate command."""
    article = assigned_article
    eo_human = _create_eo_human(create_jcom_user, eo_group, journal)
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()
    # A personal AC as older versions used to write it for each EO person
    ac_service.upsert_ac(article, eo_human.janeway_account, ac_service.HAS_UNREAD_MESSAGE, "You have unread messages")

    call_command("populate_attention_conditions", verbosity=0)

    assert (
        _unread_message_acs(article, eo_human.janeway_account).get().status == AttentionCondition.Status.RESOLVED
    ), "The personal AC of an EO person should be resolved: they share the office's one"


@pytest.mark.django_db
def test_eo_unread_message_ac_is_resolved_only_when_nothing_is_left_unread(
    assigned_article: Article,
    eo_user: JCOMProfile,
    normal_user: JCOMProfile,
):
    """The EO AC survives as long as one message of the article is not flagged as read-by-eo."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    eo = eo_user.janeway_account
    actor = normal_user.janeway_account
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    messages = [
        communication_utils.log_operation(
            article=article,
            message_subject=f"Subject {i}",
            message_body="Body",
            actor=actor,
            recipients=[editor],
            flag_as_read_by_eo=False,
        )
        for i in (1, 2)
    ]
    assert _unread_message_acs(article, eo).get().status == AttentionCondition.Status.ACTIVE

    _flag_message_read_by_eo(messages[0], read=True)
    assert (
        _unread_message_acs(article, eo).get().status == AttentionCondition.Status.ACTIVE
    ), "The AC should survive: one message is still to be read by EO"

    _flag_message_read_by_eo(messages[1], read=True)
    assert (
        _unread_message_acs(article, eo).get().status == AttentionCondition.Status.RESOLVED
    ), "The AC should be resolved: EO has read everything"


@pytest.mark.django_db
def test_flagging_a_message_read_preserves_the_other_acs(
    client: Client,
    assigned_article: Article,
    reviewer: JCOMProfile,
    normal_user: JCOMProfile,
    fake_request: HttpRequest,
    review_form: ReviewForm,  # noqa: ARG001
):
    """Flagging a message as read should not resolve the other attention conditions of the article.

    The toggle-read view re-evaluates the ACs of the article: it must do so for the FSM state
    (ArticleWorkflow.state), not for the computed one (state_value). With the computed state, no AC
    code matches the state and every time-based AC is resolved as if it were stale.
    """
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor

    # A reviewer that does not answer the invitation: this is the editor's "reviewer is late" AC
    fake_request.user = editor
    assignment = AssignToReviewer(
        workflow=article.articleworkflow,
        reviewer=reviewer.janeway_account,
        editor=editor,
        form_data={
            "acceptance_due_date": localtime(timezone.now() + timezone.timedelta(1)).strftime("%Y-%m-%d"),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()
    assignment.date_due = localtime(timezone.now()).date() - timezone.timedelta(days=1)
    assignment.save()
    for reminder in Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(assignment),
        object_id=assignment.id,
    ):
        reminder.date_sent = localtime(timezone.now()).date() - timezone.timedelta(days=3)
        reminder.save()
    attention_conditions_rebuild(article)
    reviewer_late_ac = AttentionCondition.objects.get(article=article, user=editor, code=ac_service.REVIEWER_LATE)
    assert reviewer_late_ac.status == AttentionCondition.Status.ACTIVE, "The reviewer-is-late AC should be active"

    # Drop the messages of the setup above, so that the editor has only one message to read
    Message.objects.all().delete()
    _unread_message_acs(article, editor).delete()
    message = _send_message(article, normal_user.janeway_account, editor)
    recipient_row = MessageRecipients.objects.get(message=message, recipient=editor)

    client.force_login(editor)
    response = client.post(
        reverse("wjs_message_toggle_read", args=(message.pk, editor.pk)),
        data={f"toggle-{recipient_row.pk}-read": "on"},
    )
    assert response.status_code == 200

    reviewer_late_ac.refresh_from_db()
    assert (
        reviewer_late_ac.status == AttentionCondition.Status.ACTIVE
    ), "Flagging a message as read should not resolve the reviewer-is-late AC"
    assert (
        _unread_message_acs(article, editor).get().status == AttentionCondition.Status.RESOLVED
    ), "The editor has read their only message: the unread-messages AC should be resolved"


@pytest.mark.django_db
def test_populate_covers_unread_messages_in_any_state_for_any_recipient(
    assigned_article: Article,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
):
    """The populate command heals the unread-message AC where the per-state evaluator cannot see it.

    I.e. in a state with no AC of its own (here: Accepted) and for a recipient that holds no role
    on the article (here: normal_user). The daily rebuild, on the contrary, leaves this AC alone:
    it is event-driven, and a drift should stay visible.
    """
    article = assigned_article
    workflow = article.articleworkflow
    workflow.state = ArticleWorkflow.ReviewStates.ACCEPTED
    workflow.save()
    assert workflow.state not in ACStateEvaluator.STATE_AC_MAP, "This state should have no AC of its own"
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    # A message to somebody with no role on the article: the event path creates the AC...
    message = _send_message(article, eo_user.janeway_account, normal_user.janeway_account)
    assert _unread_message_acs(article, normal_user.janeway_account).count() == 1

    # ... the daily rebuild does not bring it back if it goes missing ...
    AttentionCondition.objects.all().delete()
    call_command("rebuild_attention_conditions", verbosity=0)
    assert not _unread_message_acs(
        article, normal_user.janeway_account
    ).exists(), "The daily rebuild should leave the unread-message AC alone"

    # ... but the populate command does (e.g. after messages created by the import)
    call_command("populate_attention_conditions", verbosity=0)
    acs = _unread_message_acs(article, normal_user.janeway_account)
    assert acs.count() == 1, "populate should create the AC whatever the state and the role"
    assert acs.get().status == AttentionCondition.Status.ACTIVE

    # populate also resolves an AC left behind by a message that is gone
    message.delete()
    call_command("populate_attention_conditions", verbosity=0)
    assert (
        _unread_message_acs(article, normal_user.janeway_account).get().status == AttentionCondition.Status.RESOLVED
    ), "populate should resolve an AC whose messages are gone"


@pytest.mark.django_db
def test_attention_conditions_page_is_for_eo_only(
    client: Client,
    assigned_article: Article,
    eo_user: JCOMProfile,
    normal_user: JCOMProfile,
    create_jcom_user: Callable,
    eo_group: Group,
    journal: Journal,
):
    """The page listing all the ACs of an article is available to EO, and to EO only."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    eo_human = _create_eo_human(create_jcom_user, eo_group, journal)
    url = reverse("wjs-article-attention-conditions", args=(article.articleworkflow.pk,))

    for forbidden_user in (editor, normal_user.janeway_account, article.correspondence_author):
        client.force_login(forbidden_user)
        assert client.get(url).status_code == 403, f"{forbidden_user} should not see the attention conditions"

    client.force_login(eo_human.janeway_account)
    assert client.get(url).status_code == 200, "EO should see the attention conditions"


@pytest.mark.django_db
def test_attention_conditions_page_groups_per_user_and_orders_per_status(
    client: Client,
    assigned_article: Article,
    normal_user: JCOMProfile,
    create_jcom_user: Callable,
    eo_group: Group,
    journal: Journal,
):
    """The page shows the ACs of every user, active ones first and then by priority."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    author = article.correspondence_author
    eo_human = _create_eo_human(create_jcom_user, eo_group, journal)
    AttentionCondition.objects.all().delete()
    # For the editor: one resolved AC and two active ones, the second more urgent than the first
    resolved = ac_service.upsert_ac(article, editor, ac_service.REVIEWS_COMPLETED, "Reviews completed", priority=20)
    ac_service.resolve_ac(article, editor, ac_service.REVIEWS_COMPLETED)
    less_urgent = ac_service.upsert_ac(article, editor, ac_service.NEEDS_ASSIGNMENT, "Needs assignment", priority=20)
    more_urgent = ac_service.upsert_ac(article, editor, ac_service.REVIEWER_LATE, "Reviewer is late", priority=10)
    # ... and one for the author, to check the grouping
    author_ac = ac_service.upsert_ac(article, author, ac_service.AUTHOR_REVISION_LATE, "Revision is late", priority=10)

    client.force_login(eo_human.janeway_account)
    response = client.get(reverse("wjs-article-attention-conditions", args=(article.articleworkflow.pk,)))
    assert response.status_code == 200

    groups = dict(response.context["attention_conditions_per_user"])
    assert set(groups) == {editor, author}, "The ACs should be grouped per user"
    assert groups[author] == [author_ac], "The author's group should hold the author's AC"
    assert groups[editor] == [
        more_urgent,
        less_urgent,
        resolved,
    ], "Active ACs come first, most urgent first, and resolved ones last"


@pytest.mark.django_db
def test_unread_message_ac_priority_is_low_for_users_and_high_for_eo(
    assigned_article: Article,
    eo_user: JCOMProfile,
    normal_user: JCOMProfile,
    create_jcom_user: Callable,
    eo_group: Group,
    journal: Journal,
):
    """Unread messages come last for the people working on the paper, and first for the editorial office."""
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    eo_system_user = eo_user.janeway_account
    eo_human = _create_eo_human(create_jcom_user, eo_group, journal)
    Message.objects.all().delete()
    AttentionCondition.objects.all().delete()

    # Everybody has something to read...
    _send_message(article, normal_user.janeway_account, editor)
    # ... and something the workflow expects of them
    ac_service.upsert_ac(article, editor, ac_service.REVIEWER_LATE, "Reviewer is late", priority=10)
    ac_service.upsert_ac(article, eo_system_user, ac_service.EDITOR_IS_LATE, "Editor is late", priority=10)

    assert (
        _unread_message_acs(article, editor).get().priority == ac_service.HAS_UNREAD_MESSAGE_PRIORITY
    ), "The editor's unread-messages AC should have the lowest priority"
    assert (
        _unread_message_acs(article, eo_system_user).get().priority == ac_service.HAS_UNREAD_MESSAGE_PRIORITY_FOR_EO
    ), "The editorial office's unread-messages AC should have the highest priority"

    assert (
        _displayed_attention_condition(article, editor) == "Reviewer is late"
    ), "What the workflow expects comes first for the editor"
    assert (
        _displayed_attention_condition(article, eo_human.janeway_account) == "You have unread messages"
    ), "Messages come first for EO"
