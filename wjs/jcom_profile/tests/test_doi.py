"""Test that the generation of the DOIs respect the specs."""

# TODO: ask Iacopo why relative imports don't work... from ..utils import generate_doi
import pytest

from wjs.jcom_profile.utils import generate_doi

CASES = (
    # different sections
    (1, "01", "A01", "article", "10.22323/2.01010201"),
    (1, "01", "C01", "commentary", "10.22323/2.01010301"),
    (1, "01", "Y01", "essay", "10.22323/2.01010401"),
    (1, "01", "E01", "editorial", "10.22323/2.01010501"),
    (1, "01", "R01", "conference review", "10.22323/2.01010601"),
    (1, "01", "R01", "book review", "10.22323/2.01010701"),
    (1, "01", "N01", "practice insight", "10.22323/2.01010801"),
    (1, "01", "V01", "review article", "10.22323/2.01010901"),
    # different issues
    (1, "02", "A01", "article", "10.22323/2.01020201"),
    (1, "15", "C01", "commentary", "10.22323/2.01150301"),
    # different volume
    (2, "01", "A01", "article", "10.22323/2.02010201"),
    (11, "01", "C01", "commentary", "10.22323/2.11010301"),
    # different page numbers
    (1, "01", "A02", "article", "10.22323/2.01010202"),
    (1, "01", "C13", "commentary", "10.22323/2.01010313"),
    (1, "01", "", "editorial", "10.22323/2.01010501"),
)


@pytest.mark.parametrize(["volume", "issue", "page_numbers", "section_name", "expected_doi"], CASES)
@pytest.mark.django_db
def test_doi_generation_jcom(
    journal,
    article_factory,
    section_factory,
    issue_factory,
    volume,
    issue,
    page_numbers,
    section_name,
    expected_doi,
):
    "Generation of DOI for JCOM respect the specs."
    article = article_factory(
        journal=journal,
        page_numbers=page_numbers,
        section=section_factory(name=section_name),
    )
    issue = issue_factory(
        journal=journal,
        volume=volume,
        issue=issue,
    )
    issue.articles.add(article)
    # In previous versions, utils.generate_doi() also set the Identifier.
    # Now we don't want this.
    assert article.get_identifier("doi") is None
    generated_doi = generate_doi(article)
    assert generated_doi == expected_doi
    assert article.get_identifier("doi") is None
