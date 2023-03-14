"""Test that the analytics code set via the appropriate setting appears in some important pages."""
import faker
import pytest
from django.core.cache import cache
from django.urls import reverse
from utils import setting_handler


def list_of_target_pages(article):
    """Build a list of important pages to check."""
    pages = (
        # Published article's landing page
        reverse("article_view", kwargs={"identifier_type": "pubid", "identifier": article.get_identifier("pubid")}),
        # Issues and volumes
        reverse("journal_issues"),
        # All publications
        reverse("journal_articles"),
        # Filter by keyword
        reverse("articles_by_keyword", kwargs={"keyword": article.keywords.first().id}),
    )
    return pages


@pytest.mark.django_db
def test_analytics_code(published_articles, issue, generic_analytics_code_setting, client):
    """Set a random code and test that it's present in some important pages."""
    article = published_articles[0]

    # TODO: refactor this into article's factory or fixture
    article.authors.add(article.correspondence_author)
    article.snapshot_authors()

    # Set the script_prefix so that reverse() works properly
    client.get(f"/{article.journal.code}/")
    random_text = faker.Faker().text()
    setting_handler.save_setting(
        "general",
        "analytics_code",
        article.journal,
        random_text,
    )

    # Important!!!
    cache.clear()

    for page in list_of_target_pages(article):
        response = client.get(page)
        assert response.status_code == 200
        response_text = response.content.decode()
        assert random_text in response_text
