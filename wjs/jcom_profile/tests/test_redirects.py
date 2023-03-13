"""Test redirects."""
import re

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

    assert status_code == 301
    assert expected_redirect_url == actual_redirect_url


def url_to_label(url):
    """Return the galley label that one would expect from the give url."""
    # This is the same pattern from urlconf
    pattern = re.compile(r"(?P<pubid>[\w.()-]+?)(?:_(?P<language>[a-z]{2}))?(?P<error>_\d)?\.(?P<extension>pdf|epub)$")
    if match := re.search(pattern, url):
        label = match.group("extension").upper()
        if language := match.group("language"):
            label = f"{label} ({language})"
        return label
    return None


@pytest.mark.django_db
def test_redirect_galley_from_jcom_to_janeway_url(issue, published_article_with_standard_galleys):
    """Test redirect of simples galley/attachments/files from Drupal style."""
    article = published_article_with_standard_galleys
    pubid = article.get_identifier(identifier_type="pubid")
    # TODO: it would be nice to pytest.mark.parametrize this, but I'd
    # need the pubid from the published_article fixture...
    pesky_urls = []
    for language in ["", "_en", "_pt"]:
        for error in ["", "_0", "_1"]:
            for extension in ["pdf", "epub"]:
                pesky_urls.append(f"sites/default/files/documents/{pubid}{language}{error}.{extension}")

    client = Client()
    for pesky_url in pesky_urls:
        galley_label = url_to_label(pesky_url)
        expected_galley = Galley.objects.get(article=article, label=galley_label)

        url = f"/{article.journal.code}/{pesky_url}"
        response = client.get(url, follow=True)
        actual_redirect_url, status_code = response.redirect_chain[-1]
        assert status_code == 301

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
    url = reverse(
        "jcom_redirect_file",
        kwargs={"root": "archive/01/02/", "pubid": "nonexisting", "extension": "pdf"},
    )
    response = client.get(url, follow=True)
    assert response.status_code == 404


class TestRedirectCitationPdfUrl:
    """Galley links should appear in the same subfolder as the paper's landing page.

    An article landing page has a URL in the form:
    article/pubid/jcom_123[*]
    the galleys now have a link in the page with the form
    article/id/45/galley/67/download
    in the HTML source of this page, the citation_pdf_url should point to
    https://.../article/pubid/jcom_123/67

    Here we test that the system redirects the citation_pdf_url to the real galley URL
    article/pubid/jcom_123/67 -> article/45/galley/67/download
    for new-style URLs,
    old-style URLs,
    and old-style URLs of supplementary material (attachments)[**].

    [*] NB: the URL can be
    - article/id/ID
    - article/pubid/jcom_123
    - article/doi/10...
    I'm guessing that the 3 following should agree:
    - page URL
    - <meta name="citation_pdf_url"
    - <meta name=""citation_abstract_html_url

    [**] Technically there should be no need for this... TODO: TBV!!!
    """

    @pytest.mark.django_db
    def test_with_galley_id(self, journal, client, published_article_with_standard_galleys):
        """Test new format: article/pubid/PUBID/GALLEYID."""
        article = published_article_with_standard_galleys
        pubid = article.get_identifier(identifier_type="pubid")
        galley = article.galley_set.get(label="PDF")
        # TODO: reverse() uses the `script_prefix` which is set onto
        # the process's thread by (?) Janeway's middleware to keep
        # track of the journal (if using a path as opposet to a
        # domain) (?). The prefix is set by any call to the
        # journal. But if reverse() is called before the prefix is
        # set, it will create a URL without the journal code.
        client.get(f"/{journal.code}/")
        url = reverse(
            "jcom_redirect_file",  # ⇦ This...
            kwargs={
                "pubid": pubid,
                "galley_id": galley.id,
            },
        )
        # The above two calls are equivalent to f"/{journal.code}/article/pubid/{pubid}/{galley.id}"
        response = client.get(url, follow=True)
        actual_redirect_url, status_code = response.redirect_chain[-1]
        assert status_code == 301
        expected_redirect_url = reverse(
            "article_download_galley",  # ⇦ ...and this are *different*!
            kwargs={
                "article_id": galley.article.pk,
                "galley_id": galley.pk,
            },
        )
        assert expected_redirect_url == actual_redirect_url

    @pytest.mark.django_db
    def test_with_pubid_and_extension(self, journal, client, published_article_with_standard_galleys):
        """Test old format: article/01/01/PUBID.PDF."""
        root = "archive/01/02/"
        article = published_article_with_standard_galleys
        pubid = article.get_identifier(identifier_type="pubid")
        galley = article.galley_set.get(label="PDF")
        client.get(f"/{journal.code}/")
        url = reverse(
            "jcom_redirect_file",
            kwargs={
                "root": root,
                "pubid": pubid,
                "extension": "pdf",
            },
        )
        response = client.get(url, follow=True)
        actual_redirect_url, status_code = response.redirect_chain[-1]
        assert status_code == 301
        expected_redirect_url = reverse(
            "article_download_galley",
            kwargs={
                "article_id": galley.article.pk,
                "galley_id": galley.pk,
            },
        )
        assert expected_redirect_url == actual_redirect_url

    @pytest.mark.django_db
    def test_with_pubid_and_attachment(self, journal, client, published_article_with_standard_galleys):
        """Test old format for supplementary fiels: article/01/01/PUBID_ATTACH_N.PDF."""
        article = published_article_with_standard_galleys
        pubid = article.get_identifier(identifier_type="pubid")
        # Cheating: I just know that this article has only one supplementary file :)
        supplementary_file = article.supplementary_files.first()
        # The "attachment" part of the URL is only _ATTACH_..., without the pubid
        attachment = supplementary_file.label.replace(pubid, "")
        client.get(f"/{journal.code}/")
        url = reverse(
            "jcom_redirect_file",
            kwargs={
                # "root": ... No need! We expect all attachments in /archive/... (see urlconf)
                "pubid": pubid,
                "attachment": attachment,
            },
        )
        response = client.get(url, follow=True)
        actual_redirect_url, status_code = response.redirect_chain[-1]
        assert status_code == 301
        expected_redirect_url = reverse(
            "article_download_supp_file",
            kwargs={
                "article_id": article.pk,
                "supp_file_id": supplementary_file.pk,
            },
        )
        assert expected_redirect_url == actual_redirect_url
