"""Test the per-article full-text search used by the search page."""

import pytest
from submission import models as submission_models

from wjs.jcom_profile.search import _authors_text, _keywords_text, search_articles
from wjs.jcom_profile.tests.conftest import _create_searchable_article as _article


@pytest.fixture
def articles_qs(journal):
    return submission_models.Article.objects.filter(journal=journal)


@pytest.mark.django_db
@pytest.mark.parametrize("part", ("title", "keywords", "authors", "abstract", "full_text"))
def test_match_on_each_part(journal, articles_qs, part):
    """The term is found whichever part of the article carries it."""
    kwargs = {
        "title": {"title": "xylophone"},
        "keywords": {"keywords": ["xylophone"]},
        "authors": {"authors": [("Ann", "Xylophone")]},
        "abstract": {"abstract": "A xylophone"},
        "full_text": {"full_texts": ["the xylophone is an instrument"]},
    }[part]
    target = _article(journal, **kwargs)
    _article(journal, authors=[("Bob", "Other")], keywords=["other"], full_texts=["nothing to see"])

    assert list(search_articles(articles_qs, "xylophone")) == [target], f"{part} match not found"


@pytest.mark.django_db
def test_no_match_returns_empty(journal, articles_qs):
    _article(journal, title="xylophone")

    assert not search_articles(articles_qs, "zzzunmatched").exists(), "unmatched term returned articles"


@pytest.mark.django_db
def test_stopword_only_term_returns_empty(journal, articles_qs):
    _article(journal, title="the xylophone", full_texts=["the the the"])

    assert not search_articles(articles_qs, "the").exists(), "stop-word-only term returned articles"


@pytest.mark.django_db
def test_authors_only(journal, articles_qs):
    _article(journal, title="xylophone")
    by_author = _article(journal, authors=[("Ann", "Xylophone")])

    found = set(search_articles(articles_qs, "xylophone", authors_only=True))

    assert found == {by_author}, "authors-only search must ignore the other parts"


@pytest.mark.django_db
def test_article_without_related_parts_found_by_title(journal, articles_qs):
    target = _article(journal, title="xylophone")

    assert list(search_articles(articles_qs, "xylophone")) == [target], "missing parts nulled the vector"


@pytest.mark.django_db
def test_stored_vector_not_reparsed(journal, articles_qs):
    """Position numbers stored in the full-text vector are not searchable words."""
    target = _article(journal, full_texts=["science communication " * 20])

    assert not search_articles(articles_qs, "12").exists(), "stored positions matched as lexemes"
    assert list(search_articles(articles_qs, "communication")) == [target], "full text not searched"


@pytest.mark.django_db
def test_article_listed_once_with_many_related_rows(journal, articles_qs):
    _article(
        journal,
        authors=[("Ann", "Xylophone"), ("Bob", "Xylophone")],
        keywords=["xylophone", "xylophone music"],
        full_texts=["xylophone one", "xylophone two"],
    )

    assert search_articles(articles_qs, "xylophone").count() == 1, "article duplicated by related rows"


@pytest.mark.django_db
def test_relevance_higher_for_title_than_full_text(journal, articles_qs):
    by_title = _article(journal, title="xylophone")
    by_full_text = _article(journal, full_texts=["a xylophone"])

    relevance = {article.pk: article.relevance for article in search_articles(articles_qs, "xylophone")}

    assert relevance[by_title.pk] > relevance[by_full_text.pk], "title must weigh more than full text"


@pytest.mark.django_db
def test_search_queryset_usable_as_subquery(journal, articles_qs):
    """The search queryset can be nested in another query and still returns the correct rows."""
    target = _article(journal, full_texts=["a xylophone"])

    nested = submission_models.Article.objects.filter(pk__in=search_articles(articles_qs, "xylophone").values("pk"))

    assert list(nested) == [target], "nested search query returned wrong rows"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "term",
    ("o'reilly", "50%", "1*1", "')) OR 355=(SELECT 355 FROM PG_SLEEP(15))--", "%s", "\\"),
)
def test_hostile_terms_are_parameters(journal, articles_qs, term):
    _article(journal, title="xylophone", full_texts=["a xylophone"])

    assert isinstance(list(search_articles(articles_qs, term)), list), "hostile term broke the query"


@pytest.mark.django_db
def test_related_text_follows_a_fixed_order(journal):
    """Authors are aggregated by their order and keywords by word, whatever the insertion order."""
    article = _article(journal, authors=[("Ann", "Zeta"), ("Bob", "Alpha")], keywords=["zulu", "alpha"])
    submission_models.FrozenAuthor.objects.filter(article=article, last_name="Zeta").update(order=2)
    submission_models.FrozenAuthor.objects.filter(article=article, last_name="Alpha").update(order=1)

    row = (
        submission_models.Article.objects.filter(pk=article.pk)
        .annotate(authors_text=_authors_text(), keywords_text=_keywords_text())
        .values("authors_text", "keywords_text")
        .get()
    )

    assert row["authors_text"] == "Alpha Bob Zeta Ann", "authors not aggregated by their order"
    assert row["keywords_text"] == "alpha zulu", "keywords not aggregated by word"
