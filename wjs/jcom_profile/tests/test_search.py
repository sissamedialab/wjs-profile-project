"""Test the search UI."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from wjs.jcom_profile.tests.conftest import _create_searchable_article


@pytest.mark.parametrize(
    "forged_part",
    (
        "/search/?SearchableText=zivkovic%27",
        "/search/?page=99%27",
        "/search/?year=-1",
        "/search/?year=0",
        "/search/?year=99999",
        "/search/?year=a",
        "/search/?year=1%27",
        "/search/?sort=-date_published%27",
        "/search/?SearchableText=zivkovic%27&page=99%27&sort=-date_published%27",
        "/search/?article_search=the&sections=hOq5Ey0K",
        "/search/?article_search=the&&sections=1*1",
        # Search terms containing a literal "%" (cursor.mogrify inlines the term, then
        # .raw() makes psycopg3 re-parse "%..." as a bad placeholder). The second
        # case is a real-world double-encoded payload ("50% chance to ...").
        "/search/?article_search=50%25",
        "/search/?article_search=50%25%2520chance%2520to%2520double%2520or%2520half%2520amount",
        "/search/?article_search=100% pure",
        "/search/?article_search=%",
        # Payloads from the 03/10/2026 access log (wjs/specs#3274).
        "/search/?article_search=1*1&sort=relevance",
        "/search/?article_search=o%27reilly",
        "/search/?abstract=on&article_search=&sort=relevanceHi9BvUPf%27))"
        "%20OR%20355=(SELECT%20355%20FROM%20PG_SLEEP(15))--&title=on",
    ),
)
@pytest.mark.django_db
def test_search_view_resilient_to_hackish_inputs(journal, published_articles, client, forged_part):
    """Search view handles well forged input values."""

    url = f"/{journal.code}{forged_part}"

    response = client.get(url)
    if "page" in forged_part:
        # Django's paginator will raise a 404 if the page is out of range / not an integer
        # this is different from current behavior, but more consistent with Django practices
        assert response.status_code == 404
    else:
        assert response.status_code == 200


@pytest.mark.parametrize(
    "search_query",
    (
        "/search/?year=%s" % now().year,
        "/search/?sort=title",
        "/search/?keyword=",
        "/search/?sections=",
    ),
)
@pytest.mark.django_db
def test_search_view(journal, published_articles, client, search_query):
    """Normal queries pass validation and actually filter articles."""

    if "sections=" in search_query:
        search_query = f"{search_query}{published_articles[0].section.pk}"
        search_query = f"{search_query}&sections={published_articles[1].section.pk}"
    if "keyword=" in search_query:
        search_query = f"{search_query}{published_articles[0].keywords.first().pk}"
        search_query = f"{search_query}&keyword={published_articles[1].keywords.first().pk}"

    url = f"/{journal.code}{search_query}"

    response = client.get(url)
    assert response.status_code == 200
    assert response.context["form"].is_valid()
    results = response.context["articles"]
    if "year" in search_query:
        assert all(bool(art.date_published.year == now().year) for art in results)
    elif "sort" in search_query:
        assert results[0].title < results[1].title
    elif "keyword" in search_query:
        assert published_articles[0] in results
        assert published_articles[1] in results
    elif "sections" in search_query:
        assert published_articles[0] in results
        assert published_articles[1] in results


def _search(client, journal, **params):
    """Run the search page and return the listed articles."""
    response = client.get(f"/{journal.code}/search/", params)
    assert response.status_code == 200, "search page did not render"
    return list(response.context["articles"])


@pytest.mark.django_db
def test_search_matches_keyword(journal, client):
    """Free-text search finds articles by keyword (regression since the 1.6 form rewrite)."""
    target = _create_searchable_article(journal, keywords=["xylophone"])
    _create_searchable_article(journal, keywords=["other"])

    assert _search(client, journal, article_search="xylophone") == [target], "keyword not searched"


@pytest.mark.django_db
@pytest.mark.parametrize("sort", ("title", "-title", "date_published", "-date_published"))
def test_search_sort_with_term(journal, client, sort):
    older = _create_searchable_article(journal, title="Alpha xylophone", date_published=now() - timedelta(days=9))
    newer = _create_searchable_article(journal, title="Beta xylophone", date_published=now() - timedelta(days=2))
    expected = {
        "title": [older, newer],
        "-title": [newer, older],
        "date_published": [older, newer],
        "-date_published": [newer, older],
    }[sort]

    assert _search(client, journal, article_search="xylophone", sort=sort) == expected, f"wrong order for {sort}"


@pytest.mark.django_db
def test_search_relevance_order(journal, client):
    by_full_text = _create_searchable_article(journal, full_texts=["a xylophone"])
    by_title = _create_searchable_article(journal, title="xylophone")

    results = _search(client, journal, article_search="xylophone", sort="relevance")

    assert results == [by_title, by_full_text], "title match must rank before full-text match"


@pytest.mark.django_db
def test_search_does_not_use_raw_sql(journal, client):
    _create_searchable_article(journal, title="xylophone")

    with CaptureQueriesContext(connection) as queries:
        _search(client, journal, article_search="xylophone")

    assert not any(") AS search" in query["sql"] for query in queries.captured_queries), "raw search SQL used"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    (
        {"article_search": "xylophone", "sort": "relevance"},
        {"article_search": "xylophone", "sort": "-date_published"},
        {"sort": "title"},
    ),
)
def test_search_order_is_total(journal, client, params):
    """Ties are broken by pk, so equal dates or titles cannot move between pages."""
    _create_searchable_article(journal, title="xylophone")

    response = client.get(f"/{journal.code}/search/", params)

    assert response.context["paginator"].object_list.query.order_by[-1] == "pk", "order has no final tie-breaker"
