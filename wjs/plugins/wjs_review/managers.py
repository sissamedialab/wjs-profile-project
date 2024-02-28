from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, OuterRef, Q, QuerySet, Subquery
from review.models import ReviewAssignment
from submission.models import Article

from wjs.jcom_profile.permissions import is_eo


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

    def with_unread_messages(self, user: Account = None) -> QuerySet:
        """
        Return articles with unread messages for the current user.

        If the user is an EO, it will also return articles with :py:attr:`Message.read_by_eo` flag False.

        :param user: the user to filter the unread messages for
        :type user: Account

        :return: the queryset with unread messages
        :rtype: QuerySet
        """
        from .models import Message

        messages = Message.objects.filter(
            content_type=ContentType.objects.get_for_model(Article),
            messagerecipients__read=False,
        )
        if user:
            filters = Q(recipients__in=[user])
            if is_eo(user):
                filters |= Q(read_by_eo=False)
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
