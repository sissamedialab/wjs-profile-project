import datetime
from typing import Iterable, Optional

from core.models import AccountRole
from django.contrib.auth import get_user_model
from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.expressions import NegatedExpression
from django.db.models.functions import Least
from django.http import QueryDict
from django.utils.timezone import now
from journal.models import Journal
from submission.models import Article, Keyword, KeywordArticle
from utils.logger import get_logger

from wjs.jcom_profile import constants
from wjs.jcom_profile.models import StaffKeyword, StaffWorkloadParameters

from .models import (
    ArticleWorkflow,
    ProphyCandidate,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)

logger = get_logger(__name__)

Account = get_user_model()


def get_available_users_by_role(
    journal: Journal,
    role_slugs: list[str],
    exclude: Optional[Iterable] = None,
    filters: Optional[Q] = None,
) -> QuerySet:
    """Get users by role and journal, excluding a list of users and applying filters."""
    users_ids = AccountRole.objects.filter(
        journal=journal,
        role__slug__in=role_slugs,
    ).values_list("user_id", flat=True)
    qs = Account.objects.filter(pk__in=users_ids)
    if exclude:
        qs = qs.exclude(pk__in=exclude)
    if filters:
        qs = qs.filter(filters)
    return qs


def get_reviewers_choices(self, workflow: ArticleWorkflow) -> QuerySet[Account]:
    """Get valid reviewers choices."""
    return self.filter(is_active=True).exclude_authors(workflow)


def exclude_authors(self, workflow: ArticleWorkflow) -> QuerySet[Account]:
    """Exclude articles authors from queryset."""
    return self.exclude(
        pk__in=workflow.article_authors.values_list("pk", flat=True),
    )


def filter_reviewers(self, workflow: ArticleWorkflow, search_data: QueryDict) -> QuerySet[Account]:
    """
    Filter reviewers by input data.

    Text filter currently searches in first name, last name, email and keywords of the articles the user has reviewed.
    """
    q_filters = None
    if search_data.get("search"):
        search_text = search_data.get("search").lower()
        q_filters = Q(
            Q(first_name__icontains=search_text)
            | Q(last_name__icontains=search_text)
            | Q(middle_name__icontains=search_text)
            | Q(email__icontains=search_text)
            | Q(reviewer__article__keywords__word__icontains=search_text),
        )

    current_editor = WjsEditorAssignment.objects.get_current(workflow).editor
    # No need to exclude authors: the info is "annotated" `qs = self.exclude_authors(workflow)`
    qs = self.annotate_is_author(workflow.article)

    qs = qs.annotate_is_active_reviewer(workflow.article)
    qs = qs.annotate_is_last_round_reviewer(workflow.article)
    qs = qs.annotate_is_reviewer_available(workflow.article)

    qs = qs.annotate_has_currently_completed_review(workflow.article)
    qs = qs.annotate_has_completed_review_in_the_previous_round(workflow.article)
    qs = qs.annotate_ordering_score(current_editor)

    qs = qs.annotate_declined_current_review_round(workflow.article)
    qs = qs.annotate_declined_the_previous_review_round(workflow.article)

    qs = qs.annotate_is_prophy_candidate(workflow.article)
    qs = qs.annotate_is_only_prophy()

    # stats-like info
    qs = qs.annotate_count_reviewer_pending_reviews()
    qs = qs.annotate_count_reviewed_papers_in_timeframe(datetime.timedelta(days=365))

    if user_type := search_data.get("user_type"):
        if user_type == "known":
            qs = qs.annotate_worked_with_me(current_editor)
            qs = qs.filter(wjs_worked_with_me=True)
        elif user_type == "past":
            qs = qs.filter(wjs_has_completed_review_in_the_previous_round=True)
        elif user_type == "declined":
            qs = qs.filter(wjs_has_delined_the_previous_review_round=True)
        elif user_type == "prophy":
            qs = qs.filter(wjs_is_prophy_candidate=True)
        elif user_type == "all":
            pass
        else:
            logger.warning(f'Unknown (or not yet implemented) user_type "{user_type}"')

    if q_filters:
        qs = qs.filter(q_filters)

    if not search_data.get("search") and not search_data.get("user_type") and not user_type == "all":
        qs = qs.filter(wjs_is_author=False)
        qs = qs.filter(is_active=True)
        qs = qs.filter(wjs_is_active_reviewer=False)
        qs = qs.filter(ordering_score__gt=0)

    qs = qs.order_by("-ordering_score", "last_name").distinct()
    return qs


def annotate_is_author(self, article: Article):
    """Annotate Accounts, indicating if the person authored the given Article."""
    # one alternative: authors_of_art_x = Account.objects.filter(
    #   authors__in=(article.id,),  ⇦ Warning: misleading name: account.authors are Articles!
    #   id=OuterRef("id"))
    _filter = Article.objects.filter(pk=article.pk, frozenauthor__author=OuterRef("id"))

    return self.annotate(
        wjs_is_author=Exists(_filter),
    )


def annotate_is_active_reviewer(self, article: Article):
    """Annotate Accounts, indicating if the person is a reviewer of the given Article.

    By active reviewer we mean that the person
    - has a WorkflowReviewAssignment on this Article
    - the assignment if for the `current_review_round` of the Article
    - the assignment might or might not be declined or completed (?)

    """
    current_round = article.current_review_round()
    _filter = WorkflowReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round,
        # this will be checked elsewhere date_declined__isnull=True,
    )

    return self.annotate(
        wjs_is_active_reviewer=Exists(_filter),
    )


def annotate_is_reviewer_available(self, article: Article):
    """
    Annotate whether a reviewer is available for the given article.

    This annotation is a mix of two conditions:
    - StaffWorkloadParameters exists for the current user and its no_vacancy value (annotated in annotate_vacancy)
      is true: this is verified by filtering the annotated queryset by no_vacancy and storing as annotation
      the exists of the query (ie: a StaffWorkloadParameters exists for each user after filering by no_vacancy)
    - StaffWorkloadParameters does not exists (ie: the reviewer has never completed that section and we assume is
      available)

    :param self: Reference to the current queryset or manager instance.
    :param article: Article instance for which reviewer availability is to be checked.
    :type article: Article
    :return: Annotated queryset indicating reviewer availability.
    :rtype: QuerySet
    """

    _base = StaffWorkloadParameters.objects.filter(journal=article.journal)
    _nested_query = _base.filter(user=OuterRef("id"))
    _parameters_without_vacancy = _base.annotate_vacancy().filter(no_vacancy=True).filter(user=OuterRef("id"))
    return self.annotate(
        is_available_as_reviewer=Exists(_parameters_without_vacancy) | NegatedExpression(Exists(_nested_query)),
    )


def annotate_is_last_round_reviewer(self, article: Article):
    """Annotate Accounts, indicating if the person has been a reviewer of the given Article in the previous round.

    By past reviewer we mean that the person
    - has a WorkflowReviewAssignment on this Article
    - the assignment is on the previous round
    """
    current_round = article.current_review_round()
    _filter = WorkflowReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round - 1,
    )

    return self.annotate(
        wjs_is_last_round_reviewer=Exists(_filter),
    )


def annotate_has_completed_review_in_the_previous_round(self, article: Article):
    """Annotate Accounts, indicating if the person has completed a review in the previous round."""
    current_round = article.current_review_round()
    did_review_previously = WorkflowReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round - 1,
        date_complete__isnull=False,
        # no need for `date_declined__isnull=True`, it's redundant when date_complete is not null
        # need this because the withdraw() janeway logic sets date_complete=now()
    ).not_withdrawn()

    return self.annotate(
        wjs_has_completed_review_in_the_previous_round=Exists(did_review_previously),
    )


def annotate_has_currently_completed_review(self, article: Article):
    """Annotate Accounts, indicating if the person has a completed review for the current round."""
    current_round = article.current_review_round()
    did_review_previously = WorkflowReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round,
        date_complete__isnull=False,
        # no need for `date_declined__isnull=True`, it's redundant when date_complete is not null
    ).not_withdrawn()

    return self.annotate(
        wjs_has_currently_completed_review=Exists(did_review_previously),
    )


def annotate_declined_the_previous_review_round(self, article: Article):
    """Annotate Accounts, indicating if the person has declined an assignment in the previous review round."""
    current_round = article.current_review_round()
    _filter = WorkflowReviewAssignment.objects.filter(
        article=article.id, reviewer=OuterRef("id"), review_round__round_number=current_round - 1
    ).declined_or_withdrawn()

    return self.annotate(
        wjs_has_delined_the_previous_review_round=Exists(_filter),
    )


def annotate_declined_current_review_round(self, article: Article):
    """Annotate Accounts, indicating if the person has declined an assignment in the current review round."""
    current_round = article.current_review_round()
    _filter = WorkflowReviewAssignment.objects.filter(
        article=article.id, reviewer=OuterRef("id"), review_round__round_number=current_round
    ).declined_or_withdrawn()

    return self.annotate(
        wjs_has_declined_current_review_round=Exists(_filter),
    )


def annotate_worked_with_me(self, editor: Account):
    """Annotate Accounts, indicating if the person has ever worked with the given editor.

    At least one assignment must not be declined.
    """
    _filter = (
        WorkflowReviewAssignment.objects.not_withdrawn()
        .filter(editor=editor, reviewer=OuterRef("id"))
        .filter(date_declined__isnull=True)
    )

    return self.annotate(
        wjs_worked_with_me=Exists(_filter),
    )


def annotate_is_prophy_candidate(self, article: Article):
    """Annotate Accounts, indicating if the person is a prophy candidate for the article."""

    _filter = Subquery(
        ProphyCandidate.objects.filter(
            article=article.id,
            prophy_account__correspondence__account=OuterRef("id"),
        ),
    )

    return self.annotate(
        wjs_is_prophy_candidate=Exists(_filter),
    )


def annotate_is_only_prophy(self):
    """Annotate Accounts, indicating that the person is NOT a prophy candidate without account."""

    return self.annotate(
        wjs_is_only_prophy=Value(False),
    )


KEYWORD_WEIGHT_CAP = 100


def _keyword_score_expression(article: Article):
    """
    Build the weighted keyword score of an editor (OuterRef("pk")) for ``article``.

    score = sum over shared keywords of article_weight * min(editor_weight, 100),
    divided by sum over all article keywords of article_weight * 100. A NULL article weight
    counts as 100. The result is None when the editor shares no keyword, the article has none, or every
    article keyword has weight 0 (the denominator would be 0 and the SQL division would fail).
    Computed in a correlated subquery so the joins of the outer editors query cannot multiply it.
    """
    article_weights = {
        keyword_id: KEYWORD_WEIGHT_CAP if weight is None else weight
        for keyword_id, weight in KeywordArticle.objects.filter(article=article).values_list("keyword_id", "weight")
    }
    denominator = float(sum(article_weights.values()) * KEYWORD_WEIGHT_CAP)
    if not denominator:
        return Value(None, output_field=FloatField())
    article_weight = Case(
        *[When(keyword_id=keyword_id, then=Value(weight)) for keyword_id, weight in article_weights.items()],
        default=Value(0),
        output_field=IntegerField(),
    )
    weighted_sum = (
        StaffKeyword.objects.filter(
            parameters__user=OuterRef("pk"),
            parameters__journal=article.journal,
            keyword_id__in=list(article_weights),
        )
        .values("parameters__user")
        .annotate(total=Sum(Least(F("weight"), Value(KEYWORD_WEIGHT_CAP)) * article_weight))
        .values("total")
    )
    return ExpressionWrapper(
        Subquery(weighted_sum, output_field=IntegerField()) / Value(denominator),
        output_field=FloatField(),
    )


def get_editors_with_keywords(
    self,
    article: Article,
    current_editor: Optional[Account] = None,
    include_matching_keywords: bool = True,
) -> QuerySet[Account]:
    """
    Return the list of editors ordered by number of matching keywords they have with the article.

    Annotates each editor with ``num_shared_kwds`` (count of distinct article keywords they
    share) and ``max_matching_keyword_weight`` (the highest configured weight among those
    matches, or None if there is no match). Both are scoped to the editor's StaffWorkloadParameters
    for the article's own journal: an editor with StaffWorkloadParameters (and keywords) in a
    different journal must not have those counted here.

    Also annotates ``keyword_score``: the weighted coverage of the article's keywords by the editor's
    keywords, in 0-1, or None when the editor shares no keyword or the article has none (see
    :py:func:`_keyword_score_expression`). Same journal scoping as above.

    :param include_matching_keywords: When True (the default, used for manual editor selection),
        each returned row is also annotated with a ``.matching_keywords`` list of matched keyword
        words, at the cost of one extra query per row. Callers that only need the counts/weights
        (e.g. automatic-assignment ranking) should pass False to skip that cost.
    """
    article_keywords_ids = list(article.keywords.values_list("id", flat=True))
    keyword_match_filter = Q(
        staffworkloadparameters__journal=article.journal,
        staffworkloadparameters__staffkeyword__keyword__id__in=article_keywords_ids,
    )

    editors = self.filter(
        accountrole__role__slug__in=[constants.EDITOR_ROLE, constants.SECTION_EDITOR_ROLE],
        accountrole__journal=article.journal,
    )

    if current_editor:
        editors = editors.exclude(
            id=current_editor.id,
        )

    editors = editors.annotate(
        num_shared_kwds=Count(
            "staffworkloadparameters__staffkeyword__keyword",
            filter=keyword_match_filter,
            distinct=True,
        ),
        max_matching_keyword_weight=Max(
            "staffworkloadparameters__staffkeyword__weight",
            filter=keyword_match_filter,
        ),
    )
    editors = editors.annotate(keyword_score=_keyword_score_expression(article))

    editors = editors.annotate(
        article_editor=Count(
            "pasteditorassignment__article",
            filter=Q(pasteditorassignment__article=article),
            distinct=True,
        ),
    )

    editors = editors.order_by("-num_shared_kwds", "first_name", "last_name")

    if include_matching_keywords:
        for editor in editors:
            matching_keywords = (
                Keyword.objects.filter(
                    staffworkloadparameters__user=editor,
                    staffworkloadparameters__journal=article.journal,
                    id__in=article_keywords_ids,
                )
                .distinct()
                .values_list("word", flat=True)
            )
            editor.matching_keywords = list(matching_keywords)

    return editors


def annotate_ordering_score(self, current_editor: Account) -> QuerySet[Account]:
    return self.annotate(
        ordering_score=Case(
            When(id=current_editor.id, then=Value(2)),
            When(wjs_has_completed_review_in_the_previous_round=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )


def annotate_count_reviewer_pending_reviews(self) -> QuerySet[Account]:
    """Count the number of pending review assignments that the user has."""
    # Adapted from https://djangocentral.com/how-to-use-subquery-in-django/
    # but see also annotate_final_reviews_in_timeframe() for an alternative that might be faster.
    _pending_count = Subquery(
        WorkflowReviewAssignment.objects.pending()
        .filter(reviewer_id=OuterRef("id"))
        .values("reviewer")  # group WRA by reviewer
        .annotate(count=Count("reviewer"))
        .values("count")
    )
    return self.annotate(
        count_reviewer_pending_reviews=_pending_count,
    )


def annotate_count_reviewer_completed_reviews_in_timeframe(self, timeframe: datetime.timedelta) -> QuerySet[Account]:
    """Count the number of completed review assignments that the user did in the given timeframe."""
    # Not used as of Nov.2024, but keeping as ti might be useful in the future.
    # Adapted from https://djangocentral.com/how-to-use-subquery-in-django/
    _count = Subquery(
        WorkflowReviewAssignment.objects.completed()
        .filter(
            reviewer_id=OuterRef("id"),
            date_complete__gte=now() - timeframe,
        )
        .values("reviewer")  # group WRA by reviewer
        .annotate(count=Count("reviewer"))
        .values("count")
    )
    return self.annotate(
        count_reviewer_completed_reviews_in_timeframe=_count,
    )


def annotate_count_reviewed_papers_in_timeframe(self, timeframe: datetime.timedelta) -> QuerySet[Account]:
    """Count the number of papers with completed review assignments that the user saw in the given timeframe.

    I.e., if the reviewer saw 2 papers, but did 3 reviews (2 for one paper and 1 for the other one),
    the number will be "2".

    """
    _count = Subquery(
        WorkflowReviewAssignment.objects.completed()
        .filter(
            reviewer_id=OuterRef("id"),
            date_complete__gte=now() - timeframe,
        )
        .values("reviewer_id")  # group by reviewer
        .annotate(count=Count("article", distinct=True))  # count the distinct articles
        .values("count")
    )
    return self.annotate(
        count_reviewed_papers_in_timeframe=_count,
    )
