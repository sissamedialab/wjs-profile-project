import datetime
from collections.abc import Callable

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Max
from django.utils import timezone
from plugins.wjs_review.models import ArticleWorkflow, Message, WorkflowReviewAssignment
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile, StaffWorkloadParameters

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
        # Remember that tech_revisions do not withdraw RAs;
        # so we can have AW under technical-revision with RAs.
        # For the purpose of this assert, we can ignore them.
        assert (
            not workflow.article.current_review_round_object()
            .editorrevisionrequest_set.filter(review_round_id=last_round)
            .exclude(editor_decision__decision=ArticleWorkflow.Decisions.TECHNICAL_REVISION)
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
    assert (r1.first_name != r2.first_name) or (r1.last_name != r2.last_name)

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
        datetime.timedelta(days=1),
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
        datetime.timedelta(days=1),
    )
    assert qs.get().count_reviewed_papers_in_timeframe == 1


@pytest.mark.parametrize(
    "vacancy,expected",
    (
        ("no_start", False),
        ("no_end", False),
        ("in_range", False),
        ("out_range", True),
        ("no_dates", True),
    ),
)
@pytest.mark.django_db
def test_reviewer_is_available(user: Account, article: Article, vacancy: str, expected: bool):
    """
    Reviewer availability depends on the vacancy dates.

    This function evaluates whether a reviewer is available to review an
    article, depending on their defined vacancy start and end dates. The
    availability is determined by checking if the current date falls within
    or outside the specified range for different test cases.

    :param user: Account object representing the reviewer.
    :param article: Article object for which reviewer availability is tested.
    :param vacancy: Case scenario defining the vacancy setup.
    :param expected: Boolean value denoting expected availability result.
    :return: None
    """
    params = StaffWorkloadParameters.objects.create(
        journal=article.journal,
        user=user,
    )
    if vacancy in ["no_start", "in_range"]:
        params.vacancy_end = timezone.now() + datetime.timedelta(days=10)
    if vacancy in ["no_end", "in_range"]:
        params.vacancy_start = timezone.now() + datetime.timedelta(days=-10)
    if vacancy in ["out_range"]:
        params.vacancy_start = timezone.now() + datetime.timedelta(days=10)
        params.vacancy_end = timezone.now() + datetime.timedelta(days=20)
    params.save()
    annotated = Account.objects.annotate_is_reviewer_available(article).filter(pk=user.pk).get()
    assert annotated.is_available_as_reviewer is expected


@pytest.mark.parametrize("enabled", (True, False))
@pytest.mark.django_db
def test_reviewer_is_available_depends_on_enabled(user: Account, article: Article, enabled: bool):
    """
    Reviewer availability depends on the enabled parameter.

    This test verifies that the `is_reviewer_available` annotation correctly
    reflects the value of the `enabled` flag for a given user and article. The
    test creates a `StaffWorkloadParameters` object to simulate reviewer
    availability toggling and ensures that the output matches expectations.

    :param user: The account to be tested for reviewer availability.
    :type user: Account
    :param article: The article used for reviewer availability annotation.
    :type article: Article
    :param enabled: A flag indicating whether the reviewer is enabled or not.
    :type enabled: bool
    :return: None
    """
    StaffWorkloadParameters.objects.create(
        journal=article.journal,
        user=user,
        enabled=enabled,
    )
    annotated = Account.objects.annotate_is_reviewer_available(article).filter(pk=user.pk).get()
    assert annotated.is_available_as_reviewer is enabled


@pytest.mark.parametrize("with_staff,expected", ((True, True), (False, True)))
@pytest.mark.django_db
def test_reviewer_is_available_with_params(user: Account, article: Article, with_staff: bool, expected: bool):
    """
    Reviewer availability does not depends on the existance of StaffWorkloadParameters.

    :param user: The Account instance representing the reviewer.
    :param article: The Article instance used to assess reviewer availability.
    :param with_staff: Flag indicating whether staff workload parameters
        should be created for the reviewer.
    :param expected: Boolean representing the expected status of
        reviewer availability.
    :return: None
    """
    if with_staff:
        StaffWorkloadParameters.objects.create(
            journal=article.journal,
            user=user,
        )
    else:
        assert not StaffWorkloadParameters.objects.filter(user=user, journal=article.journal).exists()
    annotated = Account.objects.annotate_is_reviewer_available(article).filter(pk=user.pk).get()
    assert annotated.is_available_as_reviewer is expected
