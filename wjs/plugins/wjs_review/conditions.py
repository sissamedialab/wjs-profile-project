"""ArticleActions and ReviewAssignmentAction conditions.

A condition function should tell if the condition is true by returning an explanatory string. This string can be shown
to the user and should describe the situation. The idea here is to tell the user why the article / assignment requires
attention.

"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from review.models import ReviewAssignment, RevisionRequest
from submission.models import Article

from .communication_utils import get_eo_user
from .models import ArticleWorkflow, Message, WorkflowReviewAssignment
from .reminders.models import Reminder

Account = get_user_model()


def is_late(assignment: ReviewAssignment, user: Account) -> str:
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


def is_late_invitation(assignment: ReviewAssignment, user: Account) -> str:
    """Tell if the reviewer didn't even accepted/declined the assignment.

    To check if the report is late, see `is_late` above.
    """
    # TODO: use a journal setting?
    if not assignment.date_accepted and not assignment.date_declined:
        grace_period = timezone.timedelta(days=4)
        if timezone.now() - assignment.date_requested > grace_period:
            return "The reviewer has not yet answered to the invitation."

    return ""


def always(*args, **kwargs) -> str:
    """Return True 🙂."""
    return "Please check."


def review_done(assignment: ReviewAssignment, user: Account) -> str:
    """Tell if the assignement has been accepted and completed.

    Warning: assignment.is_complete is True also for declined reviews.
    Here I consider as "done" only assignments accepted and completed.
    """
    if assignment.date_accepted and assignment.is_complete:
        return "The review is ready."
    else:
        return ""


def review_not_done(assignment: ReviewAssignment, user: Account) -> str:
    """Tell if this review is not done.

    Something not-done is:
    - not accepted and not declined
    - accepted but not complete

    This is useful to filter-out actions such as "editor deselects reviewer", since it is not correct to deselect
    done-reviews and there is no gain in deselecting declined reviews.

    """
    if assignment.date_accepted and assignment.is_complete:
        return ""
    if assignment.date_declined:
        return ""
    return "Review pending."


def needs_assignment(article: Article) -> str:
    """Tell if the editor should select some reviewer.

    An article needs an assignment when
    - there are not "done" assignments
    - there are not "open" assignments

    In this situation the editor should take some action: usually select
    reviewer, but also take decision or decline assignment...
    """
    # We cannot use Article.active_reviews or comleted_reviews because they take into account all review rounds, not
    # only the current one.
    # TODO: might be able to optimize (include the review_round in the where clause below)
    review_round = article.current_review_round_object()
    assignments = ReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(
            Q(is_complete=False, date_declined__isnull=True)  # active reviews
            | Q(is_complete=True, date_declined__isnull=True),  # completed reviews
        ),
    )
    if not assignments.exists():
        return "The paper should be be assigned to some reviewer."
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
    assignments = ReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=True, date_declined__isnull=True),  # completed reviews
    )
    pending_assignments = ReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=False, date_declined__isnull=True),  # active reviews
    )
    if assignments.exists() and not pending_assignments.exists():
        return "All review assignments are ready."
    else:
        return ""


def has_unread_message(article: Article, recipient: Account) -> str:
    """
    Tell if the recipient has any unread message for the current article.

    Use :py:meth:`ArticleWorkflowQuerySet.with_unread_messages` to filter articles with current unread messages.
    """
    article_has_unread_messages = ArticleWorkflow.objects.with_unread_messages(recipient).filter(article_id=article.pk)
    if article_has_unread_messages.exists():
        return "You have unread messages."
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
        return "This article has unattended messages."
    else:
        return ""


def one_review_assignment_late(article: Article) -> str:
    """Tell if the article has one "late" review_assignment."""
    # TODO: review this condition. Is this too invasive?
    review_round = article.current_review_round_object()
    # TODO: use django.db.models.functions.Now() ?
    now = timezone.now().date()
    late_assignments = ReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=False, date_declined__isnull=True, date_due__lt=now),
    )
    if late_assignments.exists():
        return "There is a late review assignment."
    else:
        return ""


def editor_as_reviewer_is_late(article: Article) -> str:
    """Tell if the article has the editor as reviewer and the editor is "late" with the review."""
    if editor_assignment := article.editorassignment_set.order_by("assigned").last():
        editor = editor_assignment.editor
    else:
        return ""
    review_round = article.current_review_round_object()
    now = timezone.now().date()
    late_assignments = ReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round, reviewer=editor)
        & Q(is_complete=False, date_declined__isnull=True, date_due__lt=now),
    )
    if late_assignments.exists():
        return "The editor's review is late."
    else:
        return ""


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
        return f"Reviewer's reminder sent past {settings.WJS_REMINDER_LATE_AFTER} days."
    else:
        return ""


def author_revision_is_late(article: Article) -> str:
    """Tell if the author is late in submitting a revision."""
    late_revision_requests = RevisionRequest.objects.filter(
        article_id=article.id,
        date_due__lt=timezone.now().date(),
    ).order_by()
    if late_revision_requests.exists():
        expected = late_revision_requests.first().date_due
        days_late = (timezone.now().date() - expected).days
        return f"The revision request is {days_late} days late (was expected by {expected.strftime('%b-%d')})."
    else:
        return ""


def eo_has_unread_messages(article: Article) -> str:
    """
    Tell if EO has any unread message for the current article.

    Use :py:meth:`ArticleWorkflowQuerySet.with_unread_messages` to filter articles with current unread messages.
    """
    eo_user = get_eo_user(article.journal)
    return has_unread_message(article=article, recipient=eo_user)


def reviewer_report_is_late(article: Article) -> str:
    """Tell if the reviewer is late with the review."""
    # The business logic should prevent having active review assignments for past review rounds (when a revision is
    # asked, pending/unfinished assignments are withdrawn). The filter on the round should thus be superfluous.
    review_round = article.current_review_round_object()
    now = timezone.now().date()
    late_assignments = ReviewAssignment.objects.filter(
        Q(article=article, review_round=review_round)
        & Q(is_complete=False, date_declined__isnull=True, date_due__lt=now),
    )
    if late_assignments.exists():
        return "The review is late."
    else:
        return ""


def is_typesetter_late(assignment: TypesettingAssignment) -> str:
    """Tell if the typesetter is late with the assignment."""
    if timezone.now().date() >= assignment.due:
        return "Typesetting is late"
    else:
        return ""


# For some reason TypesettingAssignment.due is datetime.date, while GalleyProofing.due is datetime.datetime.
def is_author_proofing_late(assignment: GalleyProofing) -> str:
    """Tell if the author is late with the proofing assignment."""
    if assignment and timezone.now() >= assignment.due:
        return "Proofing is late"
    else:
        return ""
