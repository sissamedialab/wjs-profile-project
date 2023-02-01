import pytest
from core.models import Galley
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_redirect_issues_from_jcom_to_janeway_url(issue):
    client = Client()
    url = reverse("jcom_redirect_issue", kwargs={"volume": "01", "issue": f"{issue.issue:>02}"})
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
def test_redirect_galley_from_jcom_to_janeway_url(issue, published_article_with_standard_galleys):
    """Test redirect of simples galley/attachments/files from Drupal style."""
    article = published_article_with_standard_galleys
    pubid = article.get_identifier(identifier_type="pubid")
    for language in ["", "en", "pt"]:
        if language:
            pesky_urls = [
                f"sites/default/files/documents/{pubid}_{language}.pdf",
                f"sites/default/files/documents/{pubid}_{language}_01.pdf",
                f"sites/default/files/documents/{pubid}_{language}.epub",
                f"sites/default/files/documents/{pubid}_{language}_01.epub",
            ]
        else:
            pesky_urls = [
                f"sites/default/files/documents/{pubid}.pdf",
                f"sites/default/files/documents/{pubid}_0.pdf",
                f"sites/default/files/documents/{pubid}.epub",
                f"sites/default/files/documents/{pubid}_0.epub",
            ]
        client = Client()
        for pesky_url in pesky_urls:
            extension = pesky_url.split(".")[-1]
            galley_label = f"{extension.upper()}"
            if language:
                galley_label += f" ({language})"
            expected_galley = Galley.objects.get(article=article, label=galley_label)

            url = f"/{article.journal.code}/{pesky_url}"
            response = client.get(url, follow=True)
            actual_redirect_url, status_code = response.redirect_chain[-1]
            assert status_code == 302

            expected_redirect_url = reverse(
                "article_download_galley",
                kwargs={
                    "article_id": article.pk,
                    "galley_id": expected_galley.pk,
                },
            )
            assert expected_redirect_url == actual_redirect_url


@pytest.mark.django_db
def test_redirect_nonexistent_galley_from_jcom_to_janeway_url(journal):
    client = Client()
    url = reverse("jcom_redirect_file", kwargs={"pubid": "nonexisting", "extension": "pdf"})
    response = client.get(url, follow=True)
    assert response.status_code == 404
