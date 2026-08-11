"""ArticleActions and ReviewAssignmentAction conditions.

A condition function should tell if the condition is true by returning an explanatory string. This string can be shown
to the user and should describe the situation. The idea here is to tell the user why the article / assignment requires
attention.
"""

import datetime
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone
from journal.models import Issue, Journal
from plugins.wjs_review.models import MessageRecipients
from submission.models import REVIEW_ACCESSIBLE_STAGES, Article
from typesetting.models import GalleyProofing, TypesettingAssignment

from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.settings_helpers import get_journal_language_choices
from wjs.jcom_profile.utils import get_eo_user

from . import permissions
from .logic import states_when_article_is_considered_archived_with_under_appeal
from .models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    Message,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

Account = get_user_model()


def reviewer_is_late(article: Article, for_editor: bool = False) -> str:
    """Tell if a reviewer is late for the current article.

    For editor: fires when REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3 or
    REVIEWER_SHOULD_WRITE_REVIEW_2 has been sent (at or before now).
    For EO/director: same reminders but must have been sent at least 5 days ago.

    Only assignments with date_due in the past are considered.
    """
    now_ = timezone.now()
    time_threshold = now_ if for_editor else now_ - datetime.timedelta(days=5)

    reminder_checks = [
        (
            Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3,
            "Reviewer has not yet answered the invitation",
        ),
        (Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2, "Reviewer is late"),
    ]

    review_round = article.current_review_round_object()
    review_assignments = (
        WorkflowReviewAssignment.objects.by_current_round(article=article, review_round=review_round)
        .filter(date_due__lt=timezone.localtime(now_).date())
        .pending()
    )

    for code, message in reminder_checks:
        if any(
            assignment.all_reminders().filter(code=code, date_sent__lte=time_threshold).exists()
            for assignment in review_assignments
        ):
            return message
    return ""


def editor_is_late(article: Article) -> str:
    """Tell if an editor is late for the current article.

    Fires when EDITOR_SHOULD_MAKE_DECISION_3 has been sent at least 3 days ago.
    Only articles in review are considered.
    """
    if article.stage not in REVIEW_ACCESSIBLE_STAGES:
        return ""

    try:
        assignment = WjsEditorAssignment.objects.get_current(article)
    except WjsEditorAssignment.DoesNotExist:
        return ""
    if not assignment:
        return ""

    cut_off_date = timezone.localtime(timezone.now()).date() - datetime.timedelta(days=3)
    if Reminder.objects.filter(
        code=Reminder.ReminderCodes.EDITOR_SHOULD_MAKE_DECISION_3,
        date_sent__date__lt=cut_off_date,
        object_id=assignment.pk,
        content_type=ContentType.objects.get_for_model(assignment),
    ).exists():
        return "Editor has not yet taken a decision"
    return ""


def always(*args, **kwargs) -> str:
    """Return True 🙂."""
    return "Please check."


def review_done(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if the assignement has been accepted and completed.

    Warning: assignment.is_complete is True also for declined reviews.
    Here I consider as "done" only assignments accepted and completed.
    """
    if assignment.date_accepted and assignment.is_complete and assignment.decision != "withdrawn":
        return "The review is ready."
    else:
        return ""


def review_not_done(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if this review is not done.

    Something not-done is:
    - not accepted and not declined
    - accepted but not complete
    - withdrawn

    This is useful to filter-out actions such as "editor deselects reviewer", since it is not correct to deselect
    done-reviews and there is no gain in deselecting declined reviews.

    """
    if assignment.date_accepted and assignment.is_complete:
        return ""
    if assignment.date_declined:
        return ""
    if assignment.decision == "withdrawn":
        return ""
    return "Review pending."


def review_not_done_and_user_not_reviewer(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if this review is not done and the editor is not the reviewer."""
    return review_not_done(assignment, user) and not assignment.reviewer == user


def no_tech_revision_request(workflow: ArticleWorkflow, user: Account) -> str:
    """Tell if there is no technical revision request."""
    if not EditorRevisionRequest.objects.filter(
        article_id=workflow.article_id,
        type=ArticleWorkflow.Decisions.TECHNICAL_REVISION,
        date_completed__isnull=True,
    ).exists():
        return "No pending technical revision request."
    return ""


def review_accepted_not_completed(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if this review is not done but accepted."""
    if assignment.date_accepted and not assignment.is_complete:
        return "Review accepted but not completed."
    return ""


def review_not_accepted(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if this review assignment has not been accepted."""
    if not assignment.date_accepted and not assignment.date_declined:
        return "Review acceptance pending."
    return ""


def needs_assignment(article: Article) -> str:
    """Tell if the editor should select some reviewer.

    An article needs an assignment when
    - there are not "done" assignments
    - there are not "open" assignments

    In this situation the editor should take some action: usually select
    reviewer, but also take decision or decline assignment...

    See also below `needs_assignment_all_editorreminders_sent()`
    """
    # We cannot use Article.active_reviews or comleted_reviews because they take into account all review rounds, not
    # only the current one.
    # TODO: might be able to optimize (include the review_round in the where clause below)
    review_round = article.current_review_round_object()
    assignments = WorkflowReviewAssignment.objects.valid(article, review_round)
    if not assignments.exists():
        return "Review process should start/restart"
    else:
        return ""


def needs_assignment_all_editorreminders_sent(article: Article) -> str:
    """Tell if the paper needs a review assignment (EO/director escalated view).

    Fires when EDITOR_SHOULD_SELECT_REVIEWER_3 has been sent at least 5 days ago
    and no valid review assignments exist.
    """
    review_round = article.current_review_round_object()
    review_assignments = WorkflowReviewAssignment.objects.valid(article, review_round)
    if review_assignments.exists():
        return ""

    try:
        editor_assignment = WjsEditorAssignment.objects.get_current(article)
    except WjsEditorAssignment.DoesNotExist:
        return ""
    if not editor_assignment:
        return ""

    last_reminder_sent = Reminder.objects.filter(
        code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3.value,
        date_sent__date__lte=timezone.now() - datetime.timedelta(days=5),
        object_id=editor_assignment.id,
        content_type=ContentType.objects.get_for_model(WjsEditorAssignment),
    )
    if last_reminder_sent.exists():
        return "Review process not yet started/restarted"
    return ""


def all_assignments_completed(article: Article) -> str:
    """Tell if the editor should take a decision.

    A paper is ready for an evaluation if
    - all accepted assignments are complete
    - there is at least one complete assignment

    In this situation the editor should take decision.
    """
    # TODO: review this condition. Do we need the editor to look at the paper as soon as there is one completed
    # assignment?
    review_round = article.current_review_round_object()
    assignments = WorkflowReviewAssignment.objects.by_current_round(
        article=article, review_round=review_round
    ).completed()
    pending_assignments = WorkflowReviewAssignment.objects.by_current_round(
        article=article, review_round=review_round
    ).pending()
    if assignments.exists() and not pending_assignments.exists():
        return "Review(s) completed. Decision should be made"
    else:
        return ""


def _has_unread_message(article: Article, recipient: Account) -> bool:
    messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article.id,
    ).exclude(
        message_type=Message.MessageTypes.NOTE,
    )

    if has_eo_role(recipient):
        # for EO people, "unread" means
        # - unread-msgs to the user
        # - unread-msgs to the EO system user
        # - any msg to any user not yet read-by-eo
        filters = Q(
            Q(read_by_eo=False)
            | Q(
                messagerecipients__read=False,
                messagerecipients__recipient__in=[get_eo_user(article), recipient],
            ),
        )
    else:
        filters = Q(messagerecipients__read=False, messagerecipients__recipient=recipient)

    return messages.filter(filters).exists()


def has_unread_message(article: Article, recipient: Account) -> str:
    """Tell if the recipient has any unread message for the current article."""
    if _has_unread_message(article, recipient):
        return "You have unread messages"
    return ""


def article_has_old_unread_message(
    article: Article,
    recipient: Account | None = None,
    *,
    exclude_aus_and_revs: bool = True,
    late_after_days: int | None = None,
) -> str:
    """
    Tell if there is any message left unread for a long time.

    Please note that this function should be called only for EO/staff people, because it exposes the names of the
    recipients with overdue messages.

    :param article: the article in question;
    :param recipient: the recipient of the message;
    :param exclude_aus_and_revs: if True, ignore messages to the authors or to the reviewers of the paper. Note that an
        editor thad does I-will-review is still considered an editor, not a reviewer.
    :param late_after_days: if given, use this number of days as the threshold for
        considering a message "old". If ``None`` (the default), the threshold is
        inferred from the recipient's role: ``WJS_UNREAD_MESSAGES_LATE_AFTER_FOR_EO``
        for EO users, ``WJS_UNREAD_MESSAGES_LATE_AFTER`` otherwise.

    """
    messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article.id,
        messagerecipients__read=False,
    ).exclude(
        message_type=Message.MessageTypes.NOTE,
    )
    if late_after_days is not None:
        days = late_after_days
    elif recipient and has_eo_role(recipient):
        days = settings.WJS_UNREAD_MESSAGES_LATE_AFTER_FOR_EO
    else:
        days = settings.WJS_UNREAD_MESSAGES_LATE_AFTER
    oldest_acceptable_message_date = timezone.now() - timezone.timedelta(days=days)
    messages = messages.filter(
        created__lt=oldest_acceptable_message_date,
    )
    if exclude_aus_and_revs:
        # ignore messages whose recipient is an author of the article
        messages = messages.exclude(
            messagerecipients__recipient__in=article.author_accounts.all().values_list("id"),
        )
        messages = messages.exclude(
            messagerecipients__recipient__in=article.reviewassignment_set.all().values_list("reviewer__id"),
        )

    if messages.exists():
        late_recipients = list(
            MessageRecipients.objects.filter(message__in=messages)
            .distinct()
            .values_list("recipient__last_name", flat=True),
        )
        return f"Paper has unread messages: {', '.join(late_recipients)}"

    return ""


def one_review_assignment_late(article: Article) -> str:
    """Tell if the article has one "late" review_assignment."""
    # TODO: review this condition. Is this too invasive?
    review_round = article.current_review_round_object()
    now = timezone.now()
    late_assignments = WorkflowReviewAssignment.objects.by_current_round(
        article=article, review_round=review_round
    ).filter(
        date_due__lt=timezone.localtime(now).date(),
        is_complete=False,
        date_declined__isnull=True,
    )
    if late_assignments.exists():
        return "There is a late review assignment."
    else:
        return ""


def editor_as_reviewer_is_late(article: Article) -> str:
    """Tell if the article has the editor as reviewer and the editor is "late" with the review."""
    try:
        editor_assignment = WjsEditorAssignment.objects.get_current(article)
    except WjsEditorAssignment.DoesNotExist:
        return ""
    if editor_assignment:
        editor = editor_assignment.editor
    else:
        return ""
    review_round = article.current_review_round_object()
    now = timezone.now()
    late_assignments = WorkflowReviewAssignment.objects.by_current_round(
        article=article, review_round=review_round
    ).filter(
        date_due__lt=timezone.localtime(now).date(), is_complete=False, date_declined__isnull=True, reviewer=editor
    )
    if late_assignments.exists():
        return "Your review is overdue"
    else:
        return ""


def user_can_be_assigned_as_reviewer(workflow: ArticleWorkflow, user: Account) -> str:
    """Tell if the user is already set as reviewer of the current round."""
    article = workflow.article
    review_round = article.current_review_round_object()
    has_reviews = WorkflowReviewAssignment.objects.filter(review_round=review_round, reviewer=user).exists()
    if has_reviews:
        return ""
    else:
        return "The editor has already been assigned as reviewer."


def any_reviewer_is_late_after_reminder(article: Article) -> str:
    """Tell if reviewers stayed inactive even after extended waiting period (editor view).

    Fires when REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3 or REVIEWER_SHOULD_WRITE_REVIEW_2
    has been sent at least WJS_REMINDER_LATE_AFTER days ago for a pending, overdue assignment.
    """
    watched_reminders = (
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3.value,
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2.value,
    )
    cut_off_date = timezone.localtime(timezone.now()).date() - timezone.timedelta(
        days=settings.WJS_REMINDER_LATE_AFTER,
    )

    review_round = article.current_review_round_object()
    pending_review_assignments = (
        WorkflowReviewAssignment.objects.by_current_round(article=article, review_round=review_round)
        .filter(date_due__lt=timezone.localtime(timezone.now()).date())
        .pending()
    )
    expired_reminders = Reminder.objects.filter(
        code__in=watched_reminders,
        date_sent__date__lt=cut_off_date,
        object_id__in=pending_review_assignments.values_list("pk", flat=True),
        content_type=ContentType.objects.get_for_model(WorkflowReviewAssignment),
    )

    if expired_reminders.exists():
        return "Reviewer does not respond. Please take action"
    return ""


def author_revision_is_late(article: Article) -> str:
    """Tell if the author is late in submitting a revision.

    This is meant for the author.
    """
    late_revision_request = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        date_completed__isnull=True,
        type__in=(
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
    )
    if late_revision_request.exists():
        expected = late_revision_request.first().date_due
        days_late = (timezone.localtime(timezone.now()).date() - expected).days
        return f"The revision request is {days_late} days late (was expected by {expected})"
    else:
        return ""


def author_revision_is_late_all_reminders_sent(article: Article, late_after_days: int = 1) -> str:
    """Tell if the author is late in submitting a revision (editor/EO view).

    Fires when the post-due-date reminder (MAJOR_REVISION_2 or MINOR_REVISION_2)
    was sent at least late_after_days days ago.
    Editor: late_after_days=1. EO: late_after_days=2.
    """
    late_revision_request = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        date_completed__isnull=True,
        type__in=(
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
    )
    if revision_request := late_revision_request.first():
        watched_reminders = (
            {Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2}
            if revision_request.type == ArticleWorkflow.Decisions.MAJOR_REVISION
            else {Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_MINOR_REVISION_2}
        )
        cut_off_date = timezone.localtime(timezone.now()).date() - timezone.timedelta(days=late_after_days)
        expired_reminders = Reminder.objects.filter(
            code__in=watched_reminders,
            date_sent__date__lt=cut_off_date,
            object_id=revision_request.id,
            content_type=ContentType.objects.get_for_model(revision_request),
        )
        if expired_reminders.exists():
            expected = revision_request.date_due
            days_late = (timezone.localtime(timezone.now()).date() - expected).days
            return f"Revision is {days_late} days late. Pls consider reminding author"

    return ""


def author_technicalrevision_is_late(article: Article) -> str:
    """Tell if the author is late in submitting a technical revision.

    This is meant for the author.
    """
    late_revision_request = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        date_completed__isnull=True,
        type__in=(ArticleWorkflow.Decisions.TECHNICAL_REVISION,),
    )
    if late_revision_request.exists():
        return "Editor allowed metadata update. Please take action"
    else:
        return ""


def author_technicalrevision_is_late_all_reminders_sent(article: Article, late_after_days: int = 1) -> str:
    """Tell if the author is late in submitting a technical revision (editor/EO view).

    Fires when AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2 was sent at least
    late_after_days days ago.
    Editor: late_after_days=1. EO: late_after_days=2.
    """
    late_revision_request = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        date_completed__isnull=True,
        type__in=(ArticleWorkflow.Decisions.TECHNICAL_REVISION,),
    )
    if revision_request := late_revision_request.first():
        cut_off_date = timezone.localtime(timezone.now()).date() - timezone.timedelta(days=late_after_days)
        expired_reminders = Reminder.objects.filter(
            code__in={Reminder.ReminderCodes.AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2},
            date_sent__date__lt=cut_off_date,
            object_id=revision_request.id,
            content_type=ContentType.objects.get_for_model(revision_request),
        )
        if expired_reminders.exists():
            return "Author has not updated metadata"
    return ""


def author_appealsubmission_is_late(article: Article) -> str:
    """Tell if the author is late in submission an appeal."""
    late_revision_request = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        date_completed__isnull=True,
        type=ArticleWorkflow.Decisions.OPEN_APPEAL,
    )
    if late_revision_request.exists():
        expected = late_revision_request.first().date_due
        days_late = (timezone.localtime(timezone.now()).date() - expected).days
        # Warning: used by both author and EO, but EO appends ". Withdraw?" to this string
        # To be fixed in specs#1029
        return f"Appeal is {days_late} days late"
    else:
        return ""


def pending_revision_request(workflow: ArticleWorkflow, user: Account) -> Optional[EditorRevisionRequest]:
    """Return any pending minor/major revision if the user is author or the editor of the article."""
    if not permissions.is_article_author(workflow, user) and not permissions.is_article_editor(workflow, user):
        return None
    pending_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=workflow.article_id,
        date_completed__isnull=True,
        type__in=[
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.OPEN_APPEAL,
        ],
    ).order_by()
    if pending_revision_requests.exists():
        return pending_revision_requests.last()


def is_appeal_available(workflow: ArticleWorkflow, user: Account) -> str:
    """Check if appeal is available on the current workflow."""
    if not permissions.has_admin_role_by_article(workflow, user):
        return ""
    already_appealed = EditorDecision.objects.filter(
        workflow=workflow, decision=ArticleWorkflow.Decisions.OPEN_APPEAL
    ).exists()
    if not already_appealed:
        return "You can appeal the decision."
    else:
        return ""


def pending_edit_metadata_request(workflow: ArticleWorkflow, user: Account) -> Optional[EditorRevisionRequest]:
    """Tell if the author or the article editor have any pending technical revision."""
    if not permissions.is_article_author(workflow, user) and not permissions.is_article_editor(workflow, user):
        return None
    pending_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=workflow.article_id,
        date_completed__isnull=True,
        type=ArticleWorkflow.Decisions.TECHNICAL_REVISION,
    ).order_by()
    if pending_revision_requests.exists():
        return pending_revision_requests.last()


def reviewer_acceptdecline_is_late(article: Article) -> str:
    """Tell if the reviewer is late with evaluating (accepte/decline) the review request."""
    review_round = article.current_review_round_object()
    now = timezone.now()
    late_assignments = WorkflowReviewAssignment.objects.by_current_round(
        article=article, review_round=review_round
    ).filter(
        date_due__lt=timezone.localtime(now).date(),
        is_complete=False,
        date_accepted__isnull=True,
        date_declined__isnull=True,
    )
    if late_assignments.exists():
        return "Invite to be accepted/declined"
    else:
        return ""


def reviewer_report_is_late(article: Article) -> str:
    """Tell if the reviewer is late with the review."""
    # The business logic should prevent having active review assignments for past review rounds (when a revision is
    # asked, pending/unfinished assignments are withdrawn). The filter on the round should thus be superfluous.
    review_round = article.current_review_round_object()
    now = timezone.now()
    late_assignments = WorkflowReviewAssignment.objects.by_current_round(
        article=article, review_round=review_round
    ).filter(
        date_due__lt=timezone.localtime(now).date(),
        is_complete=False,
        date_accepted__isnull=False,
        date_declined__isnull=True,
    )
    if late_assignments.exists():
        return "Review is overdue"
    else:
        return ""


def is_typesetter_late(assignment: TypesettingAssignment, for_typesetter: bool = False) -> str:
    """Tell if the typesetter is late with the assignment.

    With for_typesetter=True the message addresses the typesetter directly
    (the AC is shown to the typesetter themselves).
    """
    if not assignment or not assignment.due:
        return ""
    today = timezone.now().date()
    if today >= assignment.due:
        days = (today - assignment.due).days
        if for_typesetter:
            return f"You are {days} days late"
        return f"Typesetter is {days} days late"
    else:
        return ""


def is_author_proofing_late(assignment: GalleyProofing) -> str:
    """Tell if the author is late with the proofing assignment."""
    if assignment and assignment.due and timezone.now() >= assignment.due:
        return (
            f"Proofing is late by {(timezone.now() - assignment.due).days} days."
            f" Was expected by {assignment.due.strftime('%F')}."
            " Please contact the author."
        )
    else:
        return ""


def can_edit_permissions_by_assignment(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """
    Tell if the user can edit permissions on the workflow.

    Permission is only available:
    - current article editor
    - director
    - EO

    :param assignment: The WorkflowReviewAssignment to check access to.
    :type assignment: WorkflowReviewAssignment
    :param user: The user to check access for.
    :type user: Account
    :return: True if the user can edit permission, False otherwise.
    :rtype: bool
    """
    if (
        assignment.article.editorassignment_set.filter(editor=user).exists()
        or permissions.has_director_role_by_article(assignment.article.articleworkflow, user)
        or permissions.has_eo_role_by_article(assignment.article.articleworkflow, user)
    ):
        return "You can edit permissions."
    else:
        return ""


def can_edit_permissions(workflow: ArticleWorkflow, user: Account) -> str:
    """
    Tell if the user can edit permissions on the workflow.

    Permission is only available:
    - current article editor
    - director
    - EO

    :param workflow: The workflow to check access to.
    :type workflow: ArticleWorkflow
    :param user: The user to check access for.
    :type user: Account
    :return: True if the user can edit permission, False otherwise.
    :rtype: bool
    """
    if (
        workflow.article.editorassignment_set.filter(editor=user).exists()
        or permissions.has_director_role_by_article(workflow.article.articleworkflow, user)
        or permissions.has_eo_role_by_article(workflow.article.articleworkflow, user)
    ):
        return "You can edit permissions."
    else:
        return ""


def journal_has_english_language(journal: Journal) -> bool:
    """
    Check if journal has english language in its available languages.

    :param journal: The journal to check access to.
    :type journal: Journal
    :return True if the journal has english language, False otherwise.
    :rtype: bool
    """
    journal_languages = get_journal_language_choices(journal)
    return "en" in [lang[0] for lang in journal_languages]


def journal_requires_english_content(journal: Journal) -> bool:
    """
    Check if journal requires english content.

    :param journal: The journal to check access to.
    :type journal: Journal
    :return True if the journal has english language, False otherwise.
    :rtype: bool
    """
    return journal.code in settings.WJS_JOURNALS_WITH_ENGLISH_CONTENT


def journal_requires_social_media_files(journal: Journal) -> bool:
    """Check if journal requires social media files (short description + image)."""
    return journal.code in settings.WJS_JOURNALS_WITH_SOCIAL_MEDIA_FILES


def article_in_special_issue(workflow: ArticleWorkflow) -> bool:
    """
    Check if the article is in a special issue.

    :param workflow: The workflow to check issue on.
    :type workflow: ArticleWorkflow
    :return True if the article is in a special issue, False otherwise.
    :rtype: bool
    """
    try:
        return workflow.article.primary_issue.issue_type.code == "collection"
    except AttributeError:
        return False


def issue_published_batch(issue: Issue) -> bool:
    """
    Check if the issue is published in batch mode.

    :param issue: The workflow to check issue on.
    :type issue: Issue
    :return True if the article is in a special issue, False otherwise.
    :rtype: bool
    """
    try:
        return issue.issueparameters.batch_publish
    except AttributeError:
        return False


def article_is_published_piecemeal(workflow: ArticleWorkflow) -> bool:
    """
    Check if the article is in an issue for which articles are published piecemeal.

    :param workflow: The workflow to check issue on.
    :type workflow: ArticleWorkflow
    :return True if the article is in a special issue, False otherwise.
    :rtype: bool
    """
    return not issue_published_batch(workflow.article.primary_issue)


def needs_article_data_for_social_media_without_translation(workflow: ArticleWorkflow, user: Account) -> bool:
    """
    Tell if the article needs social media data (short description and image) but no translations.

    Article needs social media data without translations if:
    - journal is flagged as needing social media files
    - journal does not need english language extra content
    - article is in an issue published piecemeal

    :param workflow: The workflow to check access to.
    :type workflow: ArticleWorkflow
    :param user: The user requesting the information. Not used but required by the condition function signature.
    :type user: Account
    :return True if the article needs social media data without translations, False otherwise.
    :rtype: bool
    """
    return (
        journal_requires_social_media_files(workflow.article.journal)
        and not journal_requires_english_content(workflow.article.journal)
        and article_is_published_piecemeal(workflow)
    )


def needs_article_data_for_social_media_and_translations(workflow: ArticleWorkflow, user: Account) -> bool:
    """
    Tell if the article needs social media data (short description and image) and translations.

    Article needs social media data and translations if:
    - journal is flagged as needing social media files
    - journal needs english language extra content
    - article is in an issue published piecemeal

    :param workflow: The workflow to check access to.
    :type workflow: ArticleWorkflow
    :param user: The user requesting the information. Not used but required by the condition function signature.
    :type user: Account
    :return True if the article needs social media data and translations, False otherwise.
    :rtype: bool
    """
    return (
        journal_requires_social_media_files(workflow.article.journal)
        and journal_requires_english_content(workflow.article.journal)
        and article_is_published_piecemeal(workflow)
    )


def can_withdraw_preprint(workflow: ArticleWorkflow, user: Account) -> bool:
    """Return True if the preprint can be withdrawn."""
    state_condition = workflow.state not in states_when_article_is_considered_archived_with_under_appeal
    return state_condition
