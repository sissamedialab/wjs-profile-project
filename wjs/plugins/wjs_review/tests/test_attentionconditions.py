"""Test (some) attention conditions."""

import datetime
from datetime import timedelta

import freezegun
import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from django.utils import timezone
from django.utils.timezone import localtime, now
from plugins.wjs_review import conditions, states
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

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import get_eo_user

from .conftest import _assign_article
from .test_helpers import _create_review_assignment, attention_conditions_rebuild


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

    state_cls = getattr(states, workflow.state)
    expected = localtime(expected).date()

    # author has a.c., but editor and eo don't, because reminders are not yet sent
    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent today, same a before
    updated = reminders.update(date_sent=now())
    assert updated == reminders_count
    attention_conditions_rebuild(article)
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
    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == f"The revision request is {days_past} days late (was expected by {expected})"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent more than 1 day ago, also editor has a.c.
    updated = all_reminders.update(date_sent=now() - timezone.timedelta(2))
    assert updated == reminders_count
    attention_conditions_rebuild(article)
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
    attention_conditions_rebuild(article)
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

    state_cls = getattr(states, workflow.state)
    expected = expected.date()

    # author has a.c., but editor and eo don't, because reminders are not yet sent
    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent today, same a before
    reminders.update(date_sent=now())
    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent yesterday, same as before
    all_reminders.update(date_sent=now() - timezone.timedelta(1))
    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    # all reminders sent more than 1 day ago, also editor has a.c.
    all_reminders.update(date_sent=now() - timezone.timedelta(2))
    attention_conditions_rebuild(article)
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
    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=author)
        == "Editor allowed metadata update. Please take action"
    )
    assert (
        state_cls.article_requires_attention(article=article, user=section_editor) == "Author has not updated metadata"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == "Author has not updated metadata"


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

    state_cls = getattr(states, workflow.state)

    attention_conditions_rebuild(article)
    assert state_cls.article_requires_attention(article=article, user=reviewer) == ""

    # accept/decline overdue
    assignment.date_due = localtime(timezone.now()).date() - timezone.timedelta(days=1)
    assignment.save()
    attention_conditions_rebuild(article)
    assert state_cls.article_requires_attention(article=article, user=reviewer) == "Invite to be accepted/declined"

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
        state_cls.article_requires_attention(article=article, user=section_editor)
        == "Reviewer has not yet answered the invitation"
    )
    assert state_cls.article_requires_attention(article=article, user=eo) == ""

    for reminder in reminders_list:
        reminder.date_sent = timezone.now() - datetime.timedelta(days=10)
        reminder.save()

    attention_conditions_rebuild(article)
    assert (
        state_cls.article_requires_attention(article=article, user=eo)
        == "Reviewer has not yet answered the invitation"
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
    assert state_cls.article_requires_attention(article=article, user=reviewer) == "Review is overdue"

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
    assert state_cls.article_requires_attention(article=article, user=section_editor) == "Reviewer is late"
    # EO gets "You have unread messages" because the message "reviewer accepted invite" from reviewer to editor
    # is not marked "read-by-eo"
    assert state_cls.article_requires_attention(article=article, user=eo) == "You have unread messages"

    for reminder in reminders_list:
        reminder.date_sent = timezone.now() - datetime.timedelta(days=10)
        reminder.save()

    attention_conditions_rebuild(article)
    assert state_cls.article_requires_attention(article=article, user=eo) == "Reviewer is late"

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
    assert state_cls.article_requires_attention(article=article, user=eo) == ""
    assert state_cls.article_requires_attention(article=article, user=section_editor) == ""


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


@pytest.mark.django_db
def test_old_unread_messages_excludes_aus(
    eo_user: JCOMProfile,
    normal_user: JCOMProfile,
    article: Article,
    assigned_article: Article,
):
    """
    Test conditions.article_has_old_unread_message().

    It can be forced to ignore messages from an article's author or reviewer.

    Here we will setup two articles with different authors.

    The editor of the second article is the author of the first, and has unread messages both for the first and for the
    second article.

    All messages are set with read_by_eo=True so that we can ignore that.

    """
    Message.objects.all().delete()
    article1 = article
    article2 = assigned_article

    # The first article:
    # - the unread message is for the author of the article, it should be ignored!
    # - the author is the editor of the second article
    editor2 = WjsEditorAssignment.objects.get_current(article2).editor
    author1 = editor2
    assert editor2 != normal_user
    article1.correspondence_author = author1
    article.save()
    article.authors.set([author1])  # ⇦ Important!
    long_ago = timezone.localtime(
        timezone.now() - timezone.timedelta(settings.WJS_UNREAD_MESSAGES_LATE_AFTER + 1),
    )
    m1 = Message.objects.create(
        actor=normal_user,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article1.pk,
        read_by_eo=True,
        created=long_ago,
    )
    m1.recipients.add(author1)  # ⇦ Important!
    assert MessageRecipients.objects.get(message=m1, recipient=author1).read is False
    assert conditions.article_has_old_unread_message(article1, exclude_aus_and_revs=False, recipient=normal_user)
    assert not conditions.article_has_old_unread_message(
        article1, exclude_aus_and_revs=True, recipient=normal_user
    )  # 🌟
    assert not conditions.article_has_old_unread_message(article1, recipient=eo_user)

    # The second article:
    # - the unread message is for the editor, it should be kept!
    author2 = article2.correspondence_author
    assert author2 != author1
    assert author2 != normal_user
    assert author2 != editor2
    article2.authors.set([author2])  # ⇦ Important!
    m2 = Message.objects.create(
        actor=normal_user,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article2.pk,
        read_by_eo=True,
        created=long_ago,
    )
    m2.recipients.add(editor2)  # ⇦ Important!
    assert MessageRecipients.objects.get(message=m2, recipient=editor2).read is False
    assert conditions.article_has_old_unread_message(article2, exclude_aus_and_revs=False, recipient=normal_user)
    assert conditions.article_has_old_unread_message(article2, exclude_aus_and_revs=True, recipient=normal_user)  # 🌟
    assert not conditions.article_has_old_unread_message(article2, recipient=eo_user)

    # The AW manager with_unread_messages does not exclude authors or reviewers
    qs = ArticleWorkflow.objects.with_unread_messages(user=eo_user)
    assert qs.count() == 0
    qs = ArticleWorkflow.objects.with_unread_messages(user=eo_user, other_users_messages=True)
    assert qs.count() == ArticleWorkflow.objects.all().count()


@pytest.mark.django_db
def test_with_unread_messages_excludes_revs(
    eo_user: JCOMProfile,
    normal_user: JCOMProfile,
    article: Article,
    assigned_article: Article,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    review_settings,  # noqa: ANN001, ARG001
    review_form: ReviewForm,  # noqa: ARG001
):
    """
    Test conditions.article_has_old_unread_message().

    Same as above, but test the exclusion of unread messages for the reviewer.
    """
    article1 = article
    article2 = assigned_article

    # The first article:
    # - the unread message is for the reviewer of the article, it should be ignored!
    # - the reviewer is also the editor of the second article
    editor1 = section_editor
    _assign_article(fake_request=fake_request, article=article1, section_editor=editor1)
    editor2 = WjsEditorAssignment.objects.get_current(article2).editor
    ra1 = _create_review_assignment(
        fake_request=fake_request,
        reviewer_user=editor2.jcomprofile,
        assigned_article=article1,
    )
    reviewer1 = ra1.reviewer
    author1 = article1.correspondence_author
    assert author1 != normal_user
    assert author1 != editor1
    assert author1 != reviewer1
    article.authors.set([author1])  # ⇦ Important!
    # ensure that eventual messages from the editor/reviewer assignments logic don't get in our way
    Message.objects.all().delete()
    long_ago = timezone.localtime(
        timezone.now() - timezone.timedelta(settings.WJS_UNREAD_MESSAGES_LATE_AFTER + 1),
    )
    m1 = Message.objects.create(
        actor=normal_user,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article1.pk,
        read_by_eo=True,
        created=long_ago,
    )
    m1.recipients.add(reviewer1)  # ⇦ Important!
    assert MessageRecipients.objects.get(message=m1, recipient=reviewer1).read is False
    assert conditions.article_has_old_unread_message(article1, exclude_aus_and_revs=False)
    assert not conditions.article_has_old_unread_message(article1, exclude_aus_and_revs=True)  # 🌟

    # The second article:
    # - the unread message is for the editor, it should be kept!
    author2 = article2.correspondence_author
    assert author2 != author1
    assert author2 != normal_user
    assert author2 != editor2
    assert author2 != reviewer1
    article2.authors.set([author2])  # ⇦ Important!
    m2 = Message.objects.create(
        actor=normal_user,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article2.pk,
        read_by_eo=True,
        created=long_ago,
    )
    m2.recipients.add(editor2)  # ⇦ Important!
    assert MessageRecipients.objects.get(message=m2, recipient=editor2).read is False
    assert conditions.article_has_old_unread_message(article2, exclude_aus_and_revs=False)
    assert conditions.article_has_old_unread_message(article2, exclude_aus_and_revs=True)  # 🌟

    # Check the AW manager also
    qs = ArticleWorkflow.objects.with_unread_messages(user=eo_user)
    assert qs.count() == 0
    qs = ArticleWorkflow.objects.with_unread_messages(user=eo_user, other_users_messages=True)
    assert qs.count() == len([article1, article2])


# TODO: write test for exclusions of both authors and reviewers
