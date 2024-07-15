"""Test that the generation of the DOIs respect the specs."""

# TODO: ask Iacopo why relative imports don't work... from ..utils import generate_doi
import pytest
from django.utils import timezone

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
def test_doi_generation_jcom__import(
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
    """Generation of DOI for JCOM respects the specs.

    Here we test the function that is used during import, when we receive data from wjapp.
    """
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


year = timezone.now().year
CASES = (
    # different number of "siblings" (i.e. published papers in same issue and section; tests eid)
    (1, "01", 0, "article", "10.22323/2.01010201", f"JCOM_0101_{year}_A01"),
    (1, "01", 3, "article", "10.22323/2.01010204", f"JCOM_0101_{year}_A04"),
    # different sections
    (1, "01", 0, "commentary", "10.22323/2.01010301", f"JCOM_0101_{year}_C01"),
    (1, "01", 0, "essay", "10.22323/2.01010401", f"JCOM_0101_{year}_Y01"),
    (1, "01", 0, "editorial", "10.22323/2.01010501", f"JCOM_0101_{year}_E"),
    (1, "01", 0, "conference review", "10.22323/2.01010601", f"JCOM_0101_{year}_R01"),
    (1, "01", 0, "book review", "10.22323/2.01010701", f"JCOM_0101_{year}_R01"),
    (1, "01", 0, "practice insight", "10.22323/2.01010801", f"JCOM_0101_{year}_N01"),
    (1, "01", 0, "review article", "10.22323/2.01010901", f"JCOM_0101_{year}_V01"),
)


@pytest.mark.parametrize(
    ["volume", "issue", "num_published_siblings", "section_name", "expected_doi", "expected_pubid"],
    CASES,
)
@pytest.mark.django_db
def test_doi_generation_jcom__independent(
    journal,
    jcom_doi_prefix,
    article_factory,
    section_factory,
    issue_factory,
    volume,
    issue,
    num_published_siblings,
    section_name,
    expected_doi,
    expected_pubid,
):
    """Generation of DOI for JCOM respects the specs.

    Here we test how DOIs are generated when looking only inside the system, i.e. without relying on any data from
    wjapp.

    """
    section = section_factory(name=section_name)

    issue = issue_factory(
        journal=journal,
        volume=volume,
        issue=issue,
    )

    for index in range(num_published_siblings):
        article_factory(
            title=f"Already published {section_name} - {index}",
            journal=journal,
            section=section,
            primary_issue=issue,
            date_published=timezone.now(),
        )

    article = article_factory(
        journal=journal,
        section=section,
    )
    assert article.page_numbers is None

    issue.articles.add(article)
    # NB: primary_issue must be explicitly set!
    assert article.primary_issue != issue
    article.primary_issue = issue
    article.save()

    assert article.articleworkflow.compute_doi() == expected_doi
    assert article.articleworkflow.compute_pubid() == expected_pubid

    # TODO: might want to test that the machinery that sets the identifiers works


@pytest.mark.django_db
def test_doi_generation_jcom__conference_and_book_review(
    journal,
    jcom_doi_prefix,
    article_factory,
    section_factory,
    issue_factory,
):
    """Generation of DOI for JCOM conference and book review.

    They share the same counter (!?!)
    """
    # Set the stage: an issue with two book reviews and a conference review already published
    issue = issue_factory(journal=journal, volume=1, issue="02")
    bookreview_section = section_factory(name="book review")
    conferencereview_section = section_factory(name="conference review")
    article_factory(
        title="Book review 1",
        journal=journal,
        section=bookreview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )
    article_factory(
        title="Book review 2",
        journal=journal,
        section=bookreview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )
    article_factory(
        title="Conference review 1",
        journal=journal,
        section=conferencereview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )

    # Now the test: new book or conference reviews should get counter == 4
    # TODO: ArticleWorkflow.compute_doi/pubid are stateless
    # (i.e. they compute a "new" pubid even if the article already has one)
    # I could use one of the articles created above,
    # but I prefer to simulate a scenario closer to the most common situation
    br = article_factory(
        journal=journal,
        section=bookreview_section,
        primary_issue=issue,
    )
    assert br.articleworkflow.compute_doi() == "10.22323/2.01020704"
    assert br.articleworkflow.compute_pubid() == f"JCOM_0102_{year}_R04"

    cr = article_factory(
        journal=journal,
        section=conferencereview_section,
        primary_issue=issue,
    )
    assert cr.articleworkflow.compute_doi() == "10.22323/2.01020604"
    assert cr.articleworkflow.compute_pubid() == f"JCOM_0102_{year}_R04"
