"""Full-text search on published articles for the public search page."""

import swapper
from django.contrib.postgres.aggregates import StringAgg
from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    SearchVectorCombinable,
    SearchVectorField,
)
from django.db.models import Aggregate, F, Func, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Concat
from submission.models import Article, FrozenAuthor, Keyword

#: Minimum ``ts_rank`` for an article to match; same threshold as Janeway's ``postgres_search``.
MIN_RELEVANCE = 0.01


class StoredVectorAgg(Aggregate):
    """Concatenate stored ``tsvector`` values into one ``tsvector`` without re-parsing them.

    The text form of a ``tsvector`` cast back to ``tsvector`` keeps lexemes and positions as they are; running
    ``to_tsvector`` on it instead would turn the stored positions into searchable numbers.
    """

    function = "string_agg"
    template = "%(function)s(%(expressions)s::text, ' ')::tsvector"
    output_field = SearchVectorField()


class WeightedStoredVector(SearchVectorCombinable, Func):
    """Apply a weight to an expression that is already a ``tsvector``."""

    function = "setweight"
    output_field = SearchVectorField()
    config = None

    def __init__(self, expression, weight):
        super().__init__(Coalesce(expression, Cast(Value(""), SearchVectorField())), Value(weight))


def _authors_text():
    """Return all the article's frozen author names as a single string."""
    return Subquery(
        FrozenAuthor.objects.filter(article=OuterRef("pk"))
        .order_by()
        .values("article")
        .annotate(names=StringAgg(Concat("last_name", Value(" "), "first_name"), delimiter=" ", order_by="order"))
        .values("names")
    )


def _keywords_text():
    """Return all the article's keywords as a single string."""
    return Subquery(
        Keyword.objects.filter(article=OuterRef("pk"))
        .order_by()
        .annotate(article_pk=F("article"))
        .values("article_pk")
        .annotate(words=StringAgg("word", delimiter=" ", order_by="word"))
        .values("words")
    )


def _full_text_vector():
    """Return the stored full-text vectors of all the article's galleys as a single ``tsvector``."""
    file_text_model = swapper.load_model("core", "FileText")
    return Subquery(
        file_text_model.objects.filter(file__galley__article=OuterRef("pk"))
        .order_by()
        .annotate(article_pk=F("file__galley__article"))
        .values("article_pk")
        .annotate(vector=StoredVectorAgg("contents"))
        .values("vector")
    )


def search_articles(queryset: QuerySet[Article], term: str, authors_only: bool = False) -> QuerySet[Article]:
    """
    Filter the articles matching the search term and annotate them with their ``relevance``.

    One search vector is built per article: title (A), keywords (B), authors (B), abstract (C) and the stored full
    text of its galleys (D). Related rows enter the vector through correlated subqueries, so the article is never
    multiplied by its authors, keywords or galleys, and the stored full-text vector is used as is.

    :param queryset: the articles to search in
    :param term: the search term, passed to PostgreSQL ``plainto_tsquery`` as a query parameter
    :param authors_only: search the author names only
    :return: the matching articles annotated with ``relevance``; no ordering is applied
    """
    authors = SearchVector(_authors_text(), weight="B")
    if authors_only:
        vector = authors
    else:
        vector = (
            SearchVector("title", weight="A")
            + SearchVector(_keywords_text(), weight="B")
            + authors
            + SearchVector("abstract", weight="C")
            + WeightedStoredVector(_full_text_vector(), "D")
        )
    return queryset.annotate(relevance=SearchRank(vector, SearchQuery(term))).filter(relevance__gte=MIN_RELEVANCE)
