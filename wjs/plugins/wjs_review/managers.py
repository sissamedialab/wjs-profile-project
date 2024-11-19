from typing import TYPE_CHECKING, Union

from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, OuterRef, Q, QuerySet, Subquery
from review.models import ReviewAssignment, ReviewRound
from submission.models import Article

from wjs.jcom_profile.permissions import has_eo_role

if TYPE_CHECKING:
    from .models import ArticleWorkflow, WjsEditorAssignment


class ArticleWorkflowQuerySet(models.QuerySet):
    def _latest_review_round(self, mode: str) -> Subquery:
        """
        Return a subquery to extract the latest review round. Depending on the mode, the latest review round relevant
        for Revision or the latest review round relevant for Review.

        :return: the subquery to extract the latest review round for each article
        :rtype: Subquery
        """
        isnull_condition = mode == "review"
        latest_review_round = ReviewAssignment.objects.filter(
            article=OuterRef("article_id"),
            review_round__editorrevisionrequest__isnull=isnull_condition,
        ).order_by("-review_round__round_number")
        return Subquery(latest_review_round.values("review_round")[:1])

    def _latest_review_round_number(self, mode: str) -> Subquery:
        """
        Return a subquery to extract the latest review round number. Depending on the mode, the latest review round
        relevant for Revision or the latest review round relevant for Review.

        :return: the subquery to extract the latest review round number for each article
        :rtype: Subquery
        """
        isnull_condition = mode == "review"
        latest_review_round = ReviewAssignment.objects.filter(
            article=OuterRef("article_id"),
            review_round__editorrevisionrequest__isnull=isnull_condition,
        ).order_by("-review_round__round_number")
        return Subquery(latest_review_round.values("review_round__round_number")[:1])

    def with_unread_messages(self, user: Account = None, other_users_messages: bool = False) -> QuerySet:
        """
        Returns every unread message the user has visibility on or every unread message whose recipient is the user.
        Also returns ones with read_by_eo=False for EO.
        :param user: the user to filter the unread messages for
        :type user: Account

        :param other_users_messages: filter every message the user has visibility on
        :type other_users_messages: bool

        :return: the queryset with unread messages
        :rtype: QuerySet
        """
        from .models import Message

        messages = Message.objects.filter(content_type=ContentType.objects.get_for_model(Article))
        filters = Q(messagerecipients__read=False)
        if not other_users_messages:
            filters &= Q(messagerecipients__recipient=user)
        if has_eo_role(user):
            filters |= Q(read_by_eo=False)
        messages = messages.filter(filters)
        return self.filter(article_id__in=Subquery(messages.values_list("object_id", flat=True)))

    def annotate_review_round(self, mode: str) -> QuerySet:
        """
        Provide the latest review round in every queryset object. This is useful not only for filtering, but also
        at the template level, to show the current review round number.

        You must be aware that only the review round **ID** is provided, not the review round object itself.

        Depening if called with "review" or "revision" option, returns the only latest relevant review round in each
        scenario.
        :return: the queryset with the latest review round ID and its number
        :rtype: QuerySet
        """
        queryset = self.annotate(review_round_id=self._latest_review_round(mode)).annotate(
            round_number=self._latest_review_round_number(mode)
        )
        return queryset.filter(review_round_id__isnull=False, round_number__isnull=False)

    def with_reviews(self) -> QuerySet:
        """Return ArticleWorkflow with any reviewassignment for the latest review round."""
        return self.annotate_review_round("review").filter(
            article__reviewassignment__isnull=False,
            article__reviewassignment__review_round=F("review_round_id"),
        )

    def with_pending_reviews(self) -> QuerySet:
        """Return ArticleWorkflow with pending reviewassignment for the latest review round."""
        return self.with_reviews().filter(article__reviewassignment__is_complete=False)

    def waiting_for_decision(self) -> QuerySet:
        """Return ArticleWorkflow with no pending reviewassignment for the latest review round."""
        return (
            self.with_reviews()
            .filter(state__in=["EditorSelected", "ToBeRevised"])
            .exclude(
                Q(article__reviewassignment__is_complete=False)
                | Q(article__reviewassignment__date_declined__isnull=False)
                | Q(article__reviewassignment__decision="withdrawn")
            )
        )

    def submitted_re(self) -> QuerySet:
        """Return ArticleWorkflow with no open review requests and Assigned to Editor state articles, appeal submitted
        and major/minor revisions submitted."""
        with_open_revisions = self.annotate_review_round("revision").exclude(
            article__revisionrequest__date_completed__isnull=False,
        )
        return with_open_revisions | self.filter(article__reviewassignment__isnull=True)


class WjsEditorAssignmentQuerySet(models.QuerySet):
    def get_current(self, article: Union[Article, "ArticleWorkflow"]) -> "WjsEditorAssignment":
        """
        Get the current editor assignment for the given article.

        :param article: the article to get the current editor assignment for
        :type article: Article or ArticleWorkflow

        :return: the current editor assignment
        :rtype: WjsEditorAssignment
        """
        return self.get_all(article=article).latest()

    def get_all(self, article: Union[Article, "ArticleWorkflow"]) -> QuerySet:
        """
        Get all the editor assignments for the given article.

        :param article: the article to get the editor assignments for
        :type article: Article or ArticleWorkflow

        :return: the editor assignments for the given article
        :rtype: QuerySet
        """
        from .models import ArticleWorkflow

        if isinstance(article, ArticleWorkflow):
            article = article.article
        return self.filter(article=article)


class WorkflowReviewAssignmentQuerySet(models.QuerySet):
    """FIXME: Add filter for each method."""

    def by_current_round(self, article: Article, review_round: ReviewRound) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments for the given review round.

        :param article: the article to get the valid review assignments for
        :type article: Article

        :param review_round: review round to get the valid review assignments for
        :type review_round: ReviewRound

        :return: the review assignments for the given article
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.filter(article=article, review_round=review_round)

    def valid(self, article: Article, review_round: ReviewRound) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the valid review assignments for the given article.

        We consider "valid" every assignment where the reviewer provided a report
        or that is still pending.
        I.e. all assignments that have not been declined or withdrawn.

        :param article: the article to get the valid review assignments for
        :type article: Article

        :param review_round: review round to get the valid review assignments for
        :type review_round: ReviewRound

        :return: the valid review assignments for the given article
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.active().by_current_round(article=article, review_round=review_round)

    def not_withdrawn(self) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments that are not withdrawn.

        It does not filter by article / review round, use in conjunction with :py:meth:`by_current_round`.

        :return: review assignments that are not withdrawn
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.exclude(decision="withdrawn")

    def declined_or_withdrawn(self) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments that are either withdrawn or declined.

        It does not filter by article / review round, use in conjunction with :py:meth:`by_current_round`.

        :return: review assignments that are not withdrawn
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.filter(Q(date_declined__isnull=False) | Q(decision="withdrawn"))

    def not_declined_or_withdrawn(self) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments that are not withdrawn or declined.

        It does not filter by article / review round, use in conjunction with :py:meth:`by_current_round`.

        :return: review assignments that are not withdrawn
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.exclude(Q(date_declined__isnull=False) | Q(decision="withdrawn"))

    def active(self) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments that are not completed or with a submitted review report.

        It does not filter by article / review round, use in conjunction with :py:meth:`by_current_round`.

        :return: review assignments that are not withdrawn
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.not_declined_or_withdrawn()

    def pending(self) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments that are not completed and not declined.

        It does not filter by article / review round, use in conjunction with :py:meth:`by_current_round`.

        :return: review assignments that are not withdrawn
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.filter(is_complete=False, date_declined__isnull=True)

    def completed(self) -> "WorkflowReviewAssignmentQuerySet":
        """
        Return the review assignments that are completed with a submitted review report.

        It does not filter by article / review round, use in conjunction with :py:meth:`by_current_round`.

        :return: review assignments that are not withdrawn
        :rtype: "WorkflowReviewAssignmentQuerySet"
        """
        return self.active().filter(is_complete=True)
