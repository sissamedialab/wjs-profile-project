from typing import Iterable, Optional

from core.models import AccountRole
from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef, Q, QuerySet, Subquery, Value
from django.http import QueryDict
from journal.models import Journal
from review.models import ReviewAssignment
from submission.models import Article, Keyword
from utils.logger import get_logger

from .models import ArticleWorkflow, ProphyCandidate

logger = get_logger(__name__)

Account = get_user_model()


def get_available_users_by_role(
    journal: Journal,
    role: str,
    exclude: Optional[Iterable] = None,
    filters: Optional[Q] = None,
) -> QuerySet:
    """Get users by role and journal, excluding a list of users and applying filters."""
    users_ids = AccountRole.objects.filter(
        journal=journal,
        role__slug=role,
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
            | Q(email__icontains=search_text)
            | Q(reviewer__article__keywords__word__icontains=search_text),
        )

    # No need to exclude authors: the info is "annotated" `qs = self.exclude_authors(workflow)`
    qs = self.annotate_is_author(workflow.article)

    qs = qs.annotate_is_active_reviewer(workflow.article)
    qs = qs.annotate_is_past_reviewer(workflow.article)

    qs = qs.annotate_has_currently_completed_review(workflow.article)
    qs = qs.annotate_has_previously_completed_review(workflow.article)

    qs = qs.annotate_declined_current_review_round(workflow.article)
    qs = qs.annotate_declined_previous_review_round(workflow.article)

    qs = qs.annotate_is_prophy_candidate(workflow.article)
    qs = qs.annotate_is_only_prophy()

    if user_type := search_data.get("user_type"):
        if user_type == "known":
            qs = qs.annotate_worked_with_me(
                workflow.article.current_review_round_object().reviewassignment_set.first().editor,
            )
            qs = qs.filter(wjs_worked_with_me=True)
        if user_type == "past":
            qs = qs.filter(wjs_is_past_reviewer=True)
        if user_type == "declined":
            qs = qs.filter(wjs_has_delined_previous_review_round=True)
        if user_type == "prophy":
            qs = qs.filter(wjs_is_prophy_candidate=True)
        else:
            logger.warning(f'Unknown (or not yet implemented) user_type "{user_type}"')

    if q_filters:
        qs = qs.filter(q_filters)
    # FIXME: Order must be updated once we have full annotation for user types
    return qs.order_by("-is_active", "first_name")


def annotate_is_author(self, article: Article):
    """Annotate Accounts, indicating if the person athored the given Article."""
    # one alternative: authors_of_art_x = Account.objects.filter(
    #   authors__in=(article.id,),  ⇦ Warning: misleading name: account.authors are Articles!
    #   id=OuterRef("id"))
    _filter = Article.objects.filter(id=article.id, authors=OuterRef("id"))

    return self.annotate(
        wjs_is_author=Exists(_filter),
    )


def annotate_is_active_reviewer(self, article: Article):
    """Annotate Accounts, indicating if the person is a reviewer of the given Article.

    By active reviewer we mean that the person
    - has a ReviewAssignment on this Article
    - the assignment if for the `current_review_round` of the Article
    - the assignment might or might not be declined or completed (?)

    """
    current_round = article.current_review_round()
    _filter = ReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round,
        # this will be checed elsewhere date_declined__isnull=True,
    )

    return self.annotate(
        wjs_is_active_reviewer=Exists(_filter),
    )


def annotate_is_past_reviewer(self, article: Article):
    """Annotate Accounts, indicating if the person has been a reviewer of the given Article in a previous round.

    By past reviewer we mean that the person
    - has a ReviewAssignment on this Article
    - the assignment if for on of the previous rounds (`< current_review_round`)
    """
    current_round = article.current_review_round()
    _filter = ReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number__lt=current_round,
    )

    return self.annotate(
        wjs_is_past_reviewer=Exists(_filter),
    )


def annotate_has_previously_completed_review(self, article: Article):
    """Annotate Accounts, indicating if the person has at least one completed review."""
    current_round = article.current_review_round()
    did_review_previously = ReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number__lt=current_round,
        date_complete__isnull=False,
        # no need for `date_declined__isnull=True`, it's redundant when date_complete is not null
    )

    return self.annotate(
        wjs_has_previously_completed_review=Exists(did_review_previously),
    )


def annotate_has_currently_completed_review(self, article: Article):
    """Annotate Accounts, indicating if the person has a completed review for the current round."""
    current_round = article.current_review_round()
    did_review_previously = ReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round,
        date_complete__isnull=False,
        # no need for `date_declined__isnull=True`, it's redundant when date_complete is not null
    )

    return self.annotate(
        wjs_has_currently_completed_review=Exists(did_review_previously),
    )


def annotate_declined_previous_review_round(self, article: Article):
    """Annotate Accounts, indicating if the person has declined an assignment in a previous review round."""
    current_round = article.current_review_round()
    _filter = ReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number__lt=current_round,
        date_declined__isnull=False,
    )

    return self.annotate(
        wjs_has_delined_previous_review_round=Exists(_filter),
    )


def annotate_declined_current_review_round(self, article: Article):
    """Annotate Accounts, indicating if the person has declined an assignment in the current review round."""
    current_round = article.current_review_round()
    _filter = ReviewAssignment.objects.filter(
        article=article.id,
        reviewer=OuterRef("id"),
        review_round__round_number=current_round,
        date_declined__isnull=False,
    )

    return self.annotate(
        wjs_has_declined_current_review_round=Exists(_filter),
    )


def annotate_worked_with_me(self, editor: Account):
    """Annotate Accounts, indicating if the person has ever worked with the given editor.

    At least one assignement must not be declined.
    """
    _filter = ReviewAssignment.objects.filter(
        editor=editor,
        reviewer=OuterRef("id"),
        date_declined__isnull=True,
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


def get_editors_with_keywords(self, current_editor: Account, article: Article):
    """
    Return the list of editors ordered by number of matching keywords they have with the article.
    The list of matching keywords and the count are also returned.
    """
    article_keywords_ids = list(article.keywords.values_list("id", flat=True))

    editors = self.filter(accountrole__role__slug="section-editor", accountrole__journal=article.journal).exclude(
        id=current_editor.id,
    )

    editors = editors.annotate(
        num_shared_kwds=Count(
            "editorassignmentparameters__keywords",
            filter=Q(editorassignmentparameters__keywords__id__in=article_keywords_ids),
            distinct=True,
        ),
    ).order_by("-num_shared_kwds")

    for editor in editors:
        matching_keywords = (
            Keyword.objects.filter(editorassignmentparameters__editor=editor, id__in=article_keywords_ids)
            .distinct()
            .values_list("word", flat=True)
        )
        editor.matching_keywords = list(matching_keywords)

    return editors
