"""ArticleActions and ReviewAssignmentAction conditions.

A condition function should tell if the condition is true by returning an explanatory string. This string can be shown
to the user and should describe the situation. The idea here is to tell the user why the article / assignment requires
attention.

"""

from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone
from journal.models import Issue, Journal
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from submission.models import Article

from wjs.jcom_profile.settings_helpers import get_journal_language_choices

from . import permissions
from .communication_utils import get_eo_user
from .logic import states_when_article_is_considered_archived
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


def is_late(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if the assignment is late.

    The model function `is_late` looks at the due date, i.e. we are checking if the report is late, not if the
    acceptance/declination of the assignment is late (see `is_late_invitation` below).
    """
    # NB: do not use assignment.is_late!
    # That property doesn't check if the assignment is complete, so one can have late, but complete assignments, which
    # is not what we are interested here.
    if not assignment.date_complete and timezone.now().date() >= assignment.date_due:
        return "Review assignment is late"
    else:
        return ""


def is_late_invitation(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if the reviewer didn't even accepted/declined the assignment.

    To check if the report is late, see `is_late` above.
    """
    # TODO: use a journal setting?
    if not assignment.date_accepted and not assignment.date_declined:
        grace_period = timezone.timedelta(days=4)
        if timezone.now() - assignment.date_requested > grace_period:
            return "Reviewer has not yet answered to the invitation"

    return ""


def always(*args, **kwargs) -> str:
    """Return True 🙂."""
    return "Please check."


def review_done(assignment: WorkflowReviewAssignment, user: Account) -> str:
    """Tell if the assignement has been accepted and completed.

    Warning: assignment.is_complete is True also for declined reviews.
    Here I consider as "done" only assignments accepted and completed.
    """
    if assignment.date_accepted and assignment.is_complete:
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


def no_tech_revision_request(workflow: ArticleWorkflow, user: Account) -> str:
    """Tell if there is no technical revision request."""
    if not EditorRevisionRequest.objects.filter(
        article_id=workflow.article_id,
        type=ArticleWorkflow.Decisions.TECHNICAL_REVISION,
        date_completed__isnull=True,
    ).exists():
        return "No pending technical revision request."
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
    """Tell if the paper need a review assignment.

    See above `needs_assignment()`.

    Also, take into consideration reminders to editor.
    They should all have been sent.
    """
    review_round = article.current_review_round_object()
    review_assignments = WorkflowReviewAssignment.objects.valid(article, review_round)
    if review_assignments.exists():
        return ""

    editor_assignment = WjsEditorAssignment.objects.get_current(article)
    last_reminder_sent = Reminder.objects.filter(
        code=Reminder.ReminderCodes.EDITOR_SHOULD_SELECT_REVIEWER_3.value,
        date_sent__date__lte=timezone.now().date(),
        disabled=False,
        object_id=editor_assignment.id,
        content_type=ContentType.objects.get_for_model(WjsEditorAssignment),
    )

    if last_reminder_sent.exists():
        return "Review process not yet started/restarted"
    else:
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


def has_unread_message(article: Article, recipient: Account) -> str:
    """
    Tell if the recipient has any unread message for the current article.

    Use :py:meth:`ArticleWorkflowQuerySet.with_unread_messages` to filter articles with current unread messages.
    """
    article_has_unread_messages = ArticleWorkflow.objects.with_unread_messages(
        recipient, journal=article.journal
    ).filter(article_id=article.pk)
    if article_has_unread_messages.exists():
        return "You have unread messages"
    else:
        return ""


def article_has_old_unread_message(article: Article) -> str:
    """Tell if there is any message left unread for a long time."""
    days = settings.WJS_UNREAD_MESSAGES_LATE_AFTER
    oldest_acceptable_message_date = timezone.now() - timezone.timedelta(days=days)
    unread_messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
        messagerecipients__read=False,
        created__lt=oldest_acceptable_message_date,
    )
    if unread_messages.exists():
        return "Paper has unread messages"
    else:
        return ""


def one_review_assignment_late(article: Article) -> str:
    """Tell if the article has one "late" review_assignment."""
    # TODO: review this condition. Is this too invasive?
    review_round = article.current_review_round_object()
    # TODO: use django.db.models.functions.Now() ?
    now = timezone.now().date()
    late_assignments = WorkflowReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=False, date_declined__isnull=True, date_due__lt=now),
    )
    if late_assignments.exists():
        return "There is a late review assignment."
    else:
        return ""


def editor_as_reviewer_is_late(article: Article) -> str:
    """Tell if the article has the editor as reviewer and the editor is "late" with the review."""
    if editor_assignment := WjsEditorAssignment.objects.get_current(article):
        editor = editor_assignment.editor
    else:
        return ""
    review_round = article.current_review_round_object()
    now = timezone.now().date()
    late_assignments = WorkflowReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round, reviewer=editor)
        & Q(is_complete=False, date_declined__isnull=True, date_due__lt=now),
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
    """Tell if the all reviewer's reminder for a specific condition has expired for more than a set number of days."""
    # new review round is started.
    watched_reminders = (
        Reminder.ReminderCodes.REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3.value,
        Reminder.ReminderCodes.REVIEWER_SHOULD_WRITE_REVIEW_2.value,
    )
    cut_off_date = timezone.localtime(timezone.now()).date() - timezone.timedelta(
        days=settings.WJS_REMINDER_LATE_AFTER,
    )
    pending_review_assignments = WorkflowReviewAssignment.objects.filter(
        article=article,
        is_complete=False,
    )
    expired_reminders = Reminder.objects.filter(
        code__in=watched_reminders,
        date_sent__date__lt=cut_off_date,
        disabled=False,
        object_id__in=pending_review_assignments.values_list("pk", flat=True),
        content_type=ContentType.objects.get_for_model(WorkflowReviewAssignment),
    )

    if expired_reminders.exists():
        return "Reviewer does not respond. Please take action"
    else:
        return ""


def all_reminders_sent(target, days_ago: int):
    """Return true if all reminders for the given target has been sent more that the given number of days."""
    # I'll check if there is still any reminder left to be sent (in which case it's easy)
    # and, if there are not, if the last sent reminder has been sent before yesterday.
    unsent_reminders = Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(target),
        object_id=target.id,
        disabled=False,
        date_sent__isnull=True,
    )
    if unsent_reminders.exists():
        return False

    # All reminders have been sent.
    last_sent_reminder = (
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.id,
            disabled=False,
            date_sent__isnull=False,
            # don't try `date_sent__lt=yesterday` because you might just get the first reminders
        )
        .order_by("date_sent")
        .last()
    )
    if last_sent_reminder.date_sent.date() < timezone.now().date() - timezone.timedelta(days=days_ago):
        return True
    else:
        return False


def author_revision_is_late(article: Article) -> str:
    """Tell if the author is late in submitting a revision.

    This is meant for the author.
    """
    late_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        type__in=(
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
    ).order_by()
    if late_revision_requests.exists():
        expected = late_revision_requests.first().date_due
        days_late = (timezone.now().date() - expected).days
        return f"The revision request is {days_late} days late (was expected by {expected})"
    else:
        return ""


def author_revision_is_late_all_reminders_sent(article: Article, late_after_days: int = 1) -> str:
    """Tell if the author is late in submitting a revision.

    This is intended for Editor (late_after_days=1) or EO (late_after_days=2).
    NB: if the a.c. string should be different, this needs refactoring!
    """
    late_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        type__in=(
            ArticleWorkflow.Decisions.MAJOR_REVISION,
            ArticleWorkflow.Decisions.MINOR_REVISION,
        ),
    ).order_by()
    if revision_request := late_revision_requests.last():
        if all_reminders_sent(revision_request, days_ago=late_after_days):
            expected = late_revision_requests.first().date_due
            days_late = (timezone.now().date() - expected).days
            return f"Revision is {days_late} days late. Pls consider reminding author"
    return ""


def author_technicalrevision_is_late(article: Article) -> str:
    """Tell if the author is late in submitting a technical revision.

    This is meant for the author.
    """
    late_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        type__in=(ArticleWorkflow.Decisions.TECHNICAL_REVISION,),
    ).order_by()

    if late_revision_requests.exists():
        return "Editor allowed metadata update. Please take action"
    else:
        return ""


def author_technicalrevision_is_late_all_reminders_sent(article: Article, late_after_days: int = 1) -> str:
    """Tell if the author is late in submitting a technical revision.

    "Late" means that the author has not yet set metadata after the last reminder.
    This is intended for Editor (late_after_days=1) or EO (late_after_days=2).
    """
    late_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        type__in=(ArticleWorkflow.Decisions.TECHNICAL_REVISION,),
    ).order_by()
    if revision_request := late_revision_requests.last():
        if all_reminders_sent(revision_request, days_ago=late_after_days):
            return "Author has not updated metadata"
    return ""


def author_appealsubmission_is_late(article: Article) -> str:
    """Tell if the author is late in submission an appeal."""
    late_revision_requests = EditorRevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
        type=ArticleWorkflow.Decisions.OPEN_APPEAL,
    ).order_by()
    if late_revision_requests.exists():
        expected = late_revision_requests.first().date_due
        days_late = (timezone.now().date() - expected).days
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


def eo_has_unread_messages(article: Article) -> str:
    """Tell if EO has any unread message for the current article."""
    eo_user = get_eo_user(article.journal)
    return has_unread_message(article=article, recipient=eo_user)


def reviewer_acceptdecline_is_late(article: Article) -> str:
    """Tell if the reviewer is late with evaluating (accepte/decline) the review request."""
    review_round = article.current_review_round_object()
    now = timezone.now().date()
    late_assignments = WorkflowReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=False, date_accepted__isnull=True, date_declined__isnull=True, date_due__lt=now),
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
    now = timezone.now().date()
    late_assignments = WorkflowReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=False, date_accepted__isnull=False, date_declined__isnull=True, date_due__lt=now),
    )
    if late_assignments.exists():
        return "Review is overdue"
    else:
        return ""


def is_typesetter_late(assignment: TypesettingAssignment) -> str:
    """Tell if the typesetter is late with the assignment."""
    if timezone.now().date() >= assignment.due:
        return f"Typesetter is {(timezone.now().date() - assignment.due).days} days late"
    else:
        return ""


# For some reason TypesettingAssignment.due is datetime.date, while GalleyProofing.due is datetime.datetime.
def is_author_proofing_late(assignment: GalleyProofing) -> str:
    """Tell if the author is late with the proofing assignment."""
    if assignment and timezone.now() >= assignment.due:
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


def needs_extra_article_information(workflow: ArticleWorkflow, user: Account) -> bool:
    """
    Tell if the article needs social media information.

    Article does not need social media information if either:
    - journal does not need english language extra content
    - article is in an issue published piecemeal

    :param workflow: The workflow to check access to.
    :type workflow: ArticleWorkflow
    :param user: The user requesting the information. Not used but required by the condition function signature.
    :type user: Account
    :return True if the article needs social media information, False otherwise.
    :rtype: bool
    """
    return journal_requires_english_content(workflow.article.journal) or article_is_published_piecemeal(workflow)


def can_withdraw_preprint(workflow: ArticleWorkflow, user: Account) -> bool:
    """Return True if the preprint can be withdrawn."""
    state_condition = workflow.state not in states_when_article_is_considered_archived
    return state_condition
