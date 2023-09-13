"""Test the search UI."""
import pytest


@pytest.mark.parametrize(
    "forged_part",
    (
        "/search/?SearchableText=zivkovic%27",
        "/search/?page=99%27",
        "/search/?sort=-date_published%27",
        "/search/?SearchableText=zivkovic%27&page=99%27&sort=-date_published%27",
        "/search/?article_search=the&sections=hOq5Ey0K",
        "/search/?article_search=the&&sections=1*1",
    ),
)
@pytest.mark.django_db
def test_search_view_resilient_to_hackish_inputs(journal, published_articles, client, forged_part):
    """Test that the search view handles well forged input values."""

    url = f"/{journal.code}{forged_part}"

    response = client.get(url)
    assert response.status_code == 200
