import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_redirect_issues_from_jcom_to_janeway_url(issue):
    client = Client()
    url = reverse("jcom_redirect_issue", kwargs={"volume": "1", "issue": issue.issue})
    expected_redirect_url = reverse(
        "journal_issue",
        kwargs={
            "issue_id": issue.pk,
        },
    )
    response = client.get(url, follow=True)
    actual_redirect_url, status_code = response.redirect_chain[-1]

    assert status_code == 302
    assert expected_redirect_url == actual_redirect_url


@pytest.mark.django_db
def test_redirect_article_from_jcom_to_janeway_url(issue, published_articles):
    client = Client()
    for article in published_articles:
        jcom_id = article.get_identifier("pubid")
        url = reverse("jcom_redirect_article", kwargs={"volume": "1", "issue": issue.issue, "jcom_id": jcom_id})
        expected_redirect_url = reverse(
            "article_view_custom_identifier",
            kwargs={"identifier_type": "pubid", "identifier": jcom_id},
        )
        response = client.get(url, follow=True)
        actual_redirect_url, status_code = response.redirect_chain[-1]

        assert status_code == 302
        assert expected_redirect_url == actual_redirect_url


@pytest.mark.django_db
def test_redirect_galley_from_jcom_to_janeway_url(issue, published_articles):
    client = Client()
    for article in published_articles:
        for galley in article.galley_set.all():
            url = reverse("jcom_redirect_file", kwargs={"jcom_file": galley.file.original_filename})
            expected_redirect_url = reverse(
                "article_download_galley",
                kwargs={
                    "article_id": galley.article.pk,
                    "galley_id": galley.pk,
                },
            )
            response = client.get(url, follow=True)
            actual_redirect_url, status_code = response.redirect_chain[-1]

            assert status_code == 302
            assert expected_redirect_url == actual_redirect_url


@pytest.mark.django_db
def test_redirect_nonexistent_galley_from_jcom_to_janeway_url(issue, published_articles):
    client = Client()
    url = reverse("jcom_redirect_file", kwargs={"jcom_file": "nonexistent_file.pdf"})
    response = client.get(url, follow=True)
    assert response.status_code == 404
