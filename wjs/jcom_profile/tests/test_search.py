"""Test the search UI."""

import pytest
from django.utils.timezone import now


@pytest.mark.parametrize(
    "forged_part",
    (
        "/search/?SearchableText=zivkovic%27",
        "/search/?page=99%27",
        "/search/?year=-1",
        "/search/?year=a",
        "/search/?year=1%27",
        "/search/?sort=-date_published%27",
        "/search/?SearchableText=zivkovic%27&page=99%27&sort=-date_published%27",
        "/search/?article_search=the&sections=hOq5Ey0K",
        "/search/?article_search=the&&sections=1*1",
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
