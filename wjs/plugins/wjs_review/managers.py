from typing import TYPE_CHECKING, Union

from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, OuterRef, Q, QuerySet, Subquery
from journal.models import Journal
from review.models import ReviewAssignment, ReviewRound
from submission.models import Article

from wjs.jcom_profile.permissions import has_eo_role

if TYPE_CHECKING:
    from .models import ArticleWorkflow, WjsEditorAssignment


class ArticleWorkflowQuerySet(models.QuerySet):
    def _latest_review_round(self) -> Subquery:
        """
        Return a subquery to extract the latest review round for each article.

        :return: the subquery to extract the latest review round for each article
        :rtype: Subquery
        """
        return Subquery(
            ReviewAssignment.objects.filter(article=OuterRef("article_id"))
            .order_by("-review_round__round_number")
            .values("review_round")[:1],
        )

    def _latest_review_round_number(self) -> Subquery:
        """
        Return a subquery to extract the latest review round number for each article.

        :return: the subquery to extract the latest review round number for each article
        :rtype: Subquery
        """
        return Subquery(
            ReviewAssignment.objects.filter(article=OuterRef("article_id"))
            .order_by("-review_round__round_number")
            .values("review_round__round_number")[:1],
        )

    def with_unread_messages(self, user: Account = None, journal: Journal | None = None) -> QuerySet:
        """
        Return articles with unread messages for the current user.

        If the user is an EO, it will also return articles with :py:attr:`Message.read_by_eo` flag False.

        :param user: the user to filter the unread messages for
        :type user: Account

        :param journal: the current journal
        :type journal: Journal

        :return: the queryset with unread messages
        :rtype: QuerySet
        """
        from .communication_utils import get_eo_user
        from .models import Message

        messages = Message.objects.filter(
            content_type=ContentType.objects.get_for_model(Article),
            messagerecipients__read=False,
        )
        try:
            account = user.janeway_account
        except AttributeError:
            account = user
        if account:
            is_eo_user = account == get_eo_user(journal) if journal else False
            if not is_eo_user:
                filters = Q(messagerecipients__read=False, messagerecipients__recipient=account)
            else:
                filters = Q(read_by_eo=False)
            if has_eo_role(account) and not is_eo_user:
                filters |= Q(read_by_eo=False)
            if filters:
                messages = messages.filter(filters)
        return self.filter(article_id__in=Subquery(messages.values_list("object_id", flat=True)))

    def annotate_review_round(self) -> QuerySet:
        """
        Annotate the latest review round ID and its number.

        Provide the latest review round in every queryset object. This is useful not only for filtering, but also
        at the template level, to show the current review round number.

        You must be aware that only the review round **ID** is provided, not the review round object itself.

        :return: the queryset with the latest review round ID and its number
        :rtype: QuerySet
        """
        return self.annotate(review_round_id=self._latest_review_round()).annotate(
            round_number=self._latest_review_round_number(),
        )

    def with_reviews(self) -> QuerySet:
        """Return ArticleWorkflow with any reviewassignment for the latest review round."""
        return self.annotate_review_round().filter(
            article__reviewassignment__isnull=False,
            article__reviewassignment__review_round=F("review_round_id"),
        )

    def with_pending_reviews(self) -> QuerySet:
        """Return ArticleWorkflow with pending reviewassignment for the latest review round."""
        return self.with_reviews().filter(article__reviewassignment__is_complete=False)

    def with_all_completed_reviews(self) -> QuerySet:
        """Return ArticleWorkflow with no pending reviewassignment for the latest review round."""
        return self.with_reviews().exclude(article__reviewassignment__is_complete=False)


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
