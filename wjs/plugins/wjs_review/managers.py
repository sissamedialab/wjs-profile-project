import datetime
from typing import TYPE_CHECKING, List, Union

from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import OuterRef, Q, QuerySet, Subquery
from review.models import ReviewRound
from submission.models import Article

from wjs.jcom_profile.permissions import has_eo_role

if TYPE_CHECKING:
    from .models import ArticleWorkflow, WjsEditorAssignment


class ArticleWorkflowQuerySet(models.QuerySet):
    def get_article_with_latest_round(self, mode: str = None) -> QuerySet:
        """
        Returns the queryset annotated with the latest round for each article. The method has four different modes,
        each imposing a condition on the review round. If the condition is not satisfied, the article is filtered out.
        This ensures that the logic is performed only on the last round. (We don't want to retrieve the latest round of
        an article that satisfies the condition, but rather the articles whose latest round satisfies the condition.)

        - "review": round must have a non-null ReviewAssignment
        - "revision": round must have a non-null EditorRevisionRequest
        - "no_revision_no_review": round must have both null ReviewAssignment and  EditorRevisionRequest
        - None: no condition, just the latest round

        """
        latest_round = ReviewRound.objects.filter(article=OuterRef("article_id")).order_by("-round_number")

        qs = self.annotate(
            round_number=Subquery(latest_round.values("round_number")[:1]),
            review_round_id=Subquery(latest_round.values("id")[:1]),
        )

        round_filter_conditions = {
            "review": ReviewRound.objects.filter(reviewassignment__isnull=False),
            "revision": ReviewRound.objects.filter(editorrevisionrequest__isnull=False),
            "no_revision_no_review": ReviewRound.objects.filter(
                reviewassignment__isnull=True, editorrevisionrequest__isnull=True
            ),
        }

        if mode in round_filter_conditions:
            qs = qs.filter(review_round_id__in=round_filter_conditions[mode].values("id"))

        return qs

    def with_unread_messages(
        self,
        user: Account = None,
        *,
        other_users_messages: bool = False,
    ) -> QuerySet:
        """
        Return every unread message the user has visibility on or every unread message whose recipient is the user.

        Also returns ones with read_by_eo=False for EO.
        :param user: the user to filter the unread messages for
        :type user: Account

        :param other_users_messages: filter every message the user has visibility on
        :type other_users_messages: bool

        :return: the queryset with unread messages
        :rtype: QuerySet
        """
        from .models import Message

        messages = Message.objects.filter(
            content_type=ContentType.objects.get_for_model(Article),
        ).exclude(
            message_type=Message.MessageTypes.NOTE,
        )
        filters = Q(messagerecipients__read=False)
        if not other_users_messages:
            filters &= Q(messagerecipients__recipient=user)
        if has_eo_role(user):
            filters |= Q(read_by_eo=False)

        messages = messages.filter(filters)
        return self.filter(article_id__in=Subquery(messages.values_list("object_id", flat=True)))

    def with_pending_reviews(self) -> QuerySet:
        """Return ArticleWorkflow with pending reviewassignment for the latest review round."""
        return self.get_article_with_latest_round("review").filter(article__reviewassignment__is_complete=False)

    def waiting_for_decision(self) -> QuerySet:
        """Return ArticleWorkflow in waiting for decision state."""
        state_filter = Q(state__in=["EditorSelected", "ToBeRevised"])
        with_at_least_one_completed_review = self.get_article_with_latest_round("review").filter(
            Q(article__reviewassignment__is_complete=True, article__reviewassignment__date_declined__isnull=True)
            & ~Q(article__reviewassignment__decision="withdrawn")
        )
        with_pending_reviews = self.with_pending_reviews()
        with_pending_revisions = self.get_article_with_latest_round("revision").filter(
            article__revisionrequest__date_completed__isnull=True
        )

        return with_at_least_one_completed_review.filter(
            state_filter
            & ~Q(id__in=with_pending_reviews.values_list("id", flat=True))
            & ~Q(id__in=with_pending_revisions.values_list("id", flat=True))
        )

    def submitted_re(self) -> QuerySet:
        """Return ArticleWorkflow with no open review requests and Assigned to Editor state articles, appeal submitted
        and major/minor revisions submitted."""
        only_active_revisions = self.get_article_with_latest_round("no_revision_no_review")
        return only_active_revisions.exclude(state="EditorToBeSelected")


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

    def get_final_reviews_in_timeframe(
        self, user: Account, states_list: List["ArticleWorkflow.ReviewStates"], timeframe: datetime.timedelta
    ) -> QuerySet:
        """
        Get the distinct editor assignments which are in the state_list of the review process
        in the timeframe.
        """

        states = Q(article__articleworkflow__state__in=states_list)
        return self.filter(Q(editor=user) & states & Q(assigned__gte=timeframe)).distinct()

    def get_pending_reviews_in_timeframe(
        self, user: Account, states_list: List["ArticleWorkflow.ReviewStates"], timeframe: datetime.timedelta
    ) -> QuerySet:
        """
        Get the distinct editor assignments which are not in the state_list of the review process
        in the timeframe.
        """

        states = Q(article__articleworkflow__state__in=states_list)
        return self.filter(Q(editor=user) & ~states & Q(assigned__gte=timeframe)).distinct()


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
