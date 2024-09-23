"""Test (some) attention conditions."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from django.utils import timezone
from django.utils.timezone import now
from plugins.typesetting.models import GalleyProofing
from review import models as review_models
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

from .. import communication_utils, states
from ..logic import AssignToReviewer, EvaluateReview, HandleDecision
from ..models import (
    ArticleWorkflow,
    EditorRevisionRequest,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
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
    eo = communication_utils.get_eo_user(article)

    days_past = 5
    expected = now() + timezone.timedelta(days=-days_past)  # note the "-": the author is late!
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
    revision_request = editor_decision.get_revision_request()
    article.refresh_from_db()

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

    state_cls = getattr(states, workflow.state)
    expected = expected.date()

    # author has a.c., but editor and eo don't, because reminders are not yet sent
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent today, same a before
    updated = reminders.update(date_sent=now())
    assert updated == reminders_count
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent yesterday, same as before
    # NB: using `all_reminders` because we set the date_sent above
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(1))
    assert updated == reminders_count
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent more than 1 day ago, also editor has a.c.
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(2))
    assert updated == reminders_count
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == f"Revision is {days_past} days late. Pls consider reminding author"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent more than two days ago, even EO has a.c.
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(3))
    assert updated == reminders_count
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == f"Revision is {days_past} days late. Pls consider reminding author"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=eo)
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
    eo = communication_utils.get_eo_user(article)

    days_past = 5
    expected = now() + timezone.timedelta(days=-days_past)  # note the "-": the author is late!
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
    revision_request = editor_decision.get_revision_request()
    article.refresh_from_db()

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

    state_cls = getattr(states, workflow.state)
    expected = expected.date()

    # author has a.c., but editor and eo don't, because reminders are not yet sent
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent today, same a before
    reminders.update(date_sent=now())
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent yesterday, same as before
    all_reminders.update(date_sent=now() - timezone.timedelta(1))
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent more than 1 day ago, also editor has a.c.
    all_reminders.update(date_sent=now() - timezone.timedelta(2))
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor) == "Author has not updated metadata"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent more than two days ago, even EO has a.c.
    all_reminders.update(date_sent=now() - timezone.timedelta(3))
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor) == "Author has not updated metadata"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == "Author has not updated metadata"


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
    eo = communication_utils.get_eo_user(article)
    section_editor = openappeal_err.editor
    assert section_editor == eo

    days_past = 5
    expected = now() + timezone.timedelta(days=-days_past)  # note the "-": the author is late!
    openappeal_err.date_due = expected
    openappeal_err.save()

    state_cls = getattr(states, workflow.state)
    expected = expected.date()

    assert state_cls.article_requires_attention(article=article, user=author) == f"Appeal is {days_past} days late"
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
    eo = communication_utils.get_eo_user(article)
    typesetter = assignment.round.typesettingassignment.typesetter

    days_past = 5
    expected = now() + timezone.timedelta(days=-days_past)  # note the "-": the author is late!
    assignment.due = expected
    assignment.save()

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
    eo = communication_utils.get_eo_user(article)
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
    assert state_cls.article_requires_attention(article=article, user=author) == ""
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == "Review process should start/restart"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == ""
    assert state_cls.article_requires_attention(article=article, user=director) == ""

    # all reminders sent today, a.c. for editor and director
    reminders.update(date_sent=now())
    assert state_cls.article_requires_attention(article=article, user=author) == ""
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor)
        == "Review process should start/restart"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == ""
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

    editor_assignment = WjsEditorAssignment.objects.get_current(article)
    section_editor = editor_assignment.editor

    assert not WorkflowReviewAssignment.objects.filter(article=article, reviewer=reviewer).exists()

    fake_request.user = section_editor
    assignment = AssignToReviewer(
        workflow=workflow,
        reviewer=reviewer.janeway_account,
        editor=section_editor,
        form_data={
            "acceptance_due_date": (timezone.now() + timezone.timedelta(1)).strftime("%Y-%m-%d"),
            "message": "random message",
            "author_note_visible": False,
        },
        request=fake_request,
    ).run()

    assert assignment.date_accepted is None
    assert assignment.date_due > timezone.now().date()

    state_cls = getattr(states, workflow.state)

    assert state_cls.article_requires_attention(article=article, user=reviewer) == ""

    # accept/decline overdue
    assignment.date_due = timezone.now() - timezone.timedelta(1)
    assignment.save()
    assert state_cls.article_requires_attention(article=article, user=reviewer) == "Invite to be accepted/declined"

    # report overdue
    EvaluateReview(
        assignment=assignment,
        reviewer=reviewer.janeway_account,
        editor=section_editor,
        form_data={"reviewer_decision": "1"},  # "1" means "accept"
        request=fake_request,
        token="",
    ).run()
    assert state_cls.article_requires_attention(article=article, user=reviewer) == "Review is overdue"
