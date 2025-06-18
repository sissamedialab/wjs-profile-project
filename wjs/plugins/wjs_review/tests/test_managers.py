from collections.abc import Callable

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Max
from django.utils import timezone
from plugins.wjs_review.models import ArticleWorkflow, Message, WorkflowReviewAssignment
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

Account = get_user_model()


@pytest.mark.django_db
def test_get_article_with_latest_round(create_set_of_articles_with_assignments):
    """get_article_with_latest_round manager method annotate with review round."""
    articles_with_review_round = ArticleWorkflow.objects.get_article_with_latest_round("review")
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
def test_with_pending_reviews(create_set_of_articles_with_assignments):
    """with_pending_reviews manager method filter ArticleWorkflow with any ReviewAssignment."""
    articles_with_review_round = ArticleWorkflow.objects.with_pending_reviews()
    for workflow in articles_with_review_round:
        last_round = (
            workflow.article.reviewassignment_set.all().order_by("review_round__round_number").last().review_round
        )
        assert workflow.article.reviewassignment_set.filter(review_round_id=last_round).exists()
        assert (
            not workflow.article.current_review_round_object()
            .editorrevisionrequest_set.filter(review_round_id=last_round)
            .exists()
        )
    # check that article not in the queryset do not have any open review assignment
    articles_excluded = ArticleWorkflow.objects.all().exclude(
        article__id__in=articles_with_review_round.values_list("id", flat=True),
    )
    for workflow in articles_excluded:
        if (
            last_round_assignment := workflow.article.reviewassignment_set.all()
            .order_by("review_round__round_number")
            .last()
        ):
            assert not workflow.article.reviewassignment_set.filter(
                review_round_id=last_round_assignment.review_round, is_complete=False
            ).exists()


@pytest.mark.django_db
def test_waiting_for_decision(create_set_of_articles_with_assignments):
    """with_pending_reviews manager method filter ArticleWorkflow with all complete ReviewAssignment."""
    articles_with_review_round = ArticleWorkflow.objects.waiting_for_decision()
    for workflow in articles_with_review_round:
        assert workflow.state_value == ArticleWorkflow.ReviewComputedStates.WAITING_FOR_DECISION.value


@pytest.mark.django_db
def test_with_unread_messages(
    create_set_of_articles_with_assignments: Callable,  # noqa: ARG001
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    accepted_article: Article,
):
    """
    with_unread_messages manager method filter ArticleWorkflow with at least one not unread message.

    Also if message is not read_by_eo it will be included.
    """

    messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(Article),
        messagerecipients__read=False,
    )
    articles_messages = [message.object_id for message in messages]

    articles_with_unread_messages = ArticleWorkflow.objects.with_unread_messages(normal_user)
    assert set(articles_messages) == set(articles_with_unread_messages.values_list("article_id", flat=True))
    eo_message = Message.objects.create(
        actor=eo_user,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=accepted_article.pk,
        read_by_eo=False,
    )
    eo_message.messagerecipients_set.create(recipient=eo_user, read=True)
    unread_by_eo = ArticleWorkflow.objects.with_unread_messages(user=eo_user)
    assert unread_by_eo


@pytest.mark.django_db
def test_count_reviewer_completed_reviews(journal, article_factory, account_factory):
    """Test that the manager counts correctly."""
    a1 = article_factory(journal=journal)
    a2 = article_factory(journal=journal)
    assert a1.title != a2.title

    r1 = account_factory()
    r2 = account_factory()
    assert r1.first_name != r2.first_name

    _now = timezone.now()
    a1_r1_1 = WorkflowReviewAssignment.objects.create(
        article=a1,
        reviewer=r1,
        is_complete=True,
        date_due=_now,
        date_complete=_now,
    )
    assert WorkflowReviewAssignment.objects.completed().get() == a1_r1_1
    a1_r1_2 = WorkflowReviewAssignment.objects.create(
        article=a1,
        reviewer=r1,
        is_complete=True,
        date_due=_now,
        date_complete=_now,
    )
    assert WorkflowReviewAssignment.objects.completed().count() == 2
    assert WorkflowReviewAssignment.objects.completed().order_by("-id").first() == a1_r1_2

    a2_r1_1 = WorkflowReviewAssignment.objects.create(
        article=a2,
        reviewer=r1,
        is_complete=True,
        date_due=_now,
        date_complete=_now,
    )
    assert WorkflowReviewAssignment.objects.completed().filter(article=a2).get() == a2_r1_1
    a2_r1_2_but_declined = WorkflowReviewAssignment.objects.create(
        article=a2,
        reviewer=r1,
        is_complete=False,
        date_due=_now,
        date_complete=None,
        date_declined=_now,
    )
    assert not WorkflowReviewAssignment.objects.completed().filter(id=a2_r1_2_but_declined.id).exists()

    # The assignment of the second reviewer is here only to enrich our DB
    WorkflowReviewAssignment.objects.create(
        article=a1,
        reviewer=r2,  # ⇦
        is_complete=True,
        date_due=_now,
        date_complete=_now,
    )

    # sanity check
    assert (
        WorkflowReviewAssignment.objects.filter(
            reviewer=r1,
        )
        .completed()
        .count()
        == 3
    )

    # ⋆
    qs = Account.objects.filter(
        pk=r1.id,
    ).annotate_count_reviewed_papers_in_timeframe(
        timezone.timedelta(days=1),
    )
    assert qs.get().count_reviewed_papers_in_timeframe == 2

    # let's also check r2, since it's easy
    assert (
        WorkflowReviewAssignment.objects.filter(
            reviewer=r2,
        )
        .completed()
        .count()
        == 1
    )
    qs = Account.objects.filter(
        pk=r2.id,
    ).annotate_count_reviewed_papers_in_timeframe(
        timezone.timedelta(days=1),
    )
    assert qs.get().count_reviewed_papers_in_timeframe == 1
