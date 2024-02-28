import pytest
from django.contrib.contenttypes.models import ContentType
from django.db.models import Max
from submission.models import Article

from ..models import ArticleWorkflow, Message


@pytest.mark.django_db
def test_annotate_review_round(create_set_of_articles_with_assignments):
    """annotate_review_round manager method annotate with review round."""
    articles_with_review_round = ArticleWorkflow.objects.annotate_review_round()
    for workflow in articles_with_review_round:
        # if there are any review assignments that are not complete, the round number should be the current one
        if workflow.article.reviewassignment_set.filter(date_complete__isnull=True).exists():
            assert workflow.round_number == workflow.article.current_review_round()
            assert workflow.review_round_id == workflow.article.current_review_round_object().pk
        # if any review assignment is complete, the review round id should be the max review round
        # associated with a review assignment as there might be review round without any review assignment
        # (because if there are complete review assignments
        elif workflow.article.reviewassignment_set.filter(date_complete__isnull=False).exists():
            max_review_assignment_review_round = workflow.article.reviewassignment_set.aggregate(
                max_round=Max("review_round__round_number"),
            )["max_round"]
            assert workflow.round_number == max_review_assignment_review_round
        # if there are no review assignments, the review round id is None
        else:
            assert not workflow.review_round_id
            assert not workflow.round_number


@pytest.mark.django_db
def test_with_reviews(create_set_of_articles_with_assignments):
    """with_reviews manager method filter ArticleWorkflow with any ReviewAssignment."""
    articles_with_review_round = ArticleWorkflow.objects.with_reviews()
    for workflow in articles_with_review_round:
        assert workflow.article.reviewassignment_set.filter(review_round_id=workflow.review_round_id).exists()
    # check that article not in the queryset do not have any review assignment
    articles_excluded = ArticleWorkflow.objects.all().exclude(
        article__id__in=articles_with_review_round.values_list("id", flat=True),
    )
    for workflow in articles_excluded:
        assert not workflow.article.reviewassignment_set.exists()


@pytest.mark.django_db
def test_with_pending_reviews(create_set_of_articles_with_assignments):
    """with_pending_reviews manager method filter ArticleWorkflow with any incomplete ReviewAssignment."""
    articles_with_review_round = ArticleWorkflow.objects.with_pending_reviews()
    for workflow in articles_with_review_round:
        assert workflow.article.reviewassignment_set.filter(
            review_round_id=workflow.review_round_id,
            is_complete=False,
        ).exists()


@pytest.mark.django_db
def test_with_all_completed_reviews(create_set_of_articles_with_assignments):
    """with_pending_reviews manager method filter ArticleWorkflow with all complete ReviewAssignment."""
    articles_with_review_round = ArticleWorkflow.objects.with_all_completed_reviews()
    for workflow in articles_with_review_round:
        assert workflow.article.reviewassignment_set.filter(
            review_round_id=workflow.review_round_id,
            is_complete=True,
        ).exists()


@pytest.mark.django_db
def test_with_unread_messages(create_set_of_articles_with_assignments):
    """with_unread_messages manager method filter ArticleWorkflow with at least one not unread message."""
    messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(Article),
        messagerecipients__read=False,
    )
    articles_messages = [message.object_id for message in messages]

    articles_with_review_round = ArticleWorkflow.objects.with_unread_messages()
    assert set(articles_messages) == set(articles_with_review_round.values_list("article_id", flat=True))
