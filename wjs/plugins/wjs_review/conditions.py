"""ArticleActions and ReviewAssignmentAction conditions.

A condition function should tell if the condition is true by returning an explanatory string. This string can be shown
to the user and should describe the situation. The idea here is to tell the user why the article / assignment requires
attention.

"""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.timezone import now, timedelta
from review.models import ReviewAssignment
from submission.models import Article

Account = get_user_model()


def is_late(assignment: ReviewAssignment, user: Account) -> str:
    """Tell if the assignment is late.

    The model function `is_late` looks at the due date, i.e. we are checking if the report is late, not if the
    acceptance/declination of the assignment is late (see `is_late_invitation` below).
    """
    # NB: do not use assignment.is_late!
    # That property doesn't check if the assignment is complete, so one can have late, but complete assignments, which
    # is not what we are interested here.
    if not assignment.date_complete and now().date() >= assignment.date_due:
        return "Review assignment is late"
    else:
        return ""


def is_late_invitation(assignment: ReviewAssignment, user: Account) -> str:
    """Tell if the reviewer didn't even accepted/declined the assignment.

    To check if the report is late, see `is_late` above.
    """
    # TODO: use a journal setting?
    if not assignment.date_accepted and not assignment.date_declined:
        grace_period = timedelta(days=4)
        if now() - assignment.date_requested > grace_period:
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
        return "The paper has not yet been assigned to any reviewer."
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
