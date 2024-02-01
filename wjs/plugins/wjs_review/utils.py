from django.db.models import QuerySet
from review.models import ReviewAssignment

from .models import WorkflowReviewAssignment


def get_other_review_assignments_for_this_round(
    review_assignment: ReviewAssignment,
) -> QuerySet[WorkflowReviewAssignment]:
    """Return a queryset of ReviewAssigments for the same article/round of the given review_assigment.

    The queryset does not include the give review_assigment.

    This is useful because after actions such as accept/decline review assignment or submit review or others we decide
    whether to create/delete some editor reminder based on the presence/state of other review assignments on the
    article.

    """
    # Janeway's article.active_reviews and similar do _not_ consider the review round, and, even if the business
    # logic should prevent any issue concerning reminders (i.e. when a new round is created, all reminders are
    # dealt with), we should look only at the review assignments of the current round.

    # Not using `article.current_review_round_object()` should hit the db once less.
    review_round = review_assignment.workflowreviewassignment.review_round
    my_id = review_assignment.workflowreviewassignment.id
    other_assignments_for_this_round = (
        WorkflowReviewAssignment.objects.filter(
            article=review_assignment.article,
            editor=review_assignment.editor,
            review_round=review_round,
        )
        .exclude(id=my_id)
        .exclude(decision="withdrawn")
    )
    return other_assignments_for_this_round
