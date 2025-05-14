"""Test that the generation of the DOIs respect the specs."""

# TODO: ask Iacopo why relative imports don't work... from ..utils import generate_doi
from collections.abc import Callable

import freezegun
import pytest
from django.utils import timezone
from identifiers.logic import get_dois_for_articles
from journal.models import Journal
from plugins.wjs_review.models import ArticleWorkflow
from utils import setting_handler

from wjs.jcom_profile.constants import JCOM_SECTION_TO_PUBIDSECTIONCODE
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


@pytest.mark.skip(reason="Not importing from wjapp anymore.")
@pytest.mark.parametrize({"volume", "issue", "page_numbers", "section_name", "expected_doi"}, CASES)
@pytest.mark.django_db
def test_doi_generation_jcom__import(
    journal: Journal,
    article_factory: Callable,
    section_factory: Callable,
    issue_factory: Callable,
    volume: int,
    issue: str,
    page_numbers: str,
    section_name: str,
    expected_doi: str,
):
    """
    Generation of DOI for JCOM respects the specs.

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
    (1, "01", 0, "article", f"JCOM_0101_{year}_A01"),
    (1, "01", 3, "article", f"JCOM_0101_{year}_A04"),
    # different sections
    (1, "01", 0, "commentary", f"JCOM_0101_{year}_C01"),
    (1, "01", 0, "essay", f"JCOM_0101_{year}_Y01"),
    (1, "01", 0, "editorial", f"JCOM_0101_{year}_E"),
    (1, "01", 0, "conference review", f"JCOM_0101_{year}_R01"),
    (1, "01", 0, "book review", f"JCOM_0101_{year}_R01"),
    (1, "01", 0, "practice insight", f"JCOM_0101_{year}_N01"),
    (1, "01", 0, "review article", f"JCOM_0101_{year}_V01"),
)


@pytest.mark.parametrize(
    ("volume", "issue", "num_published_siblings", "section_name", "expected_pubid"),
    CASES,
)
@pytest.mark.django_db
def test_doi_generation_jcom__independent(
    journal: Journal,
    jcom_doi_prefix: Callable,  # noqa: ARG001
    article_factory: Callable,
    section_factory: Callable,
    issue_factory: Callable,
    volume: int,
    issue: str,
    num_published_siblings: int,
    section_name: str,
    expected_pubid: str,
    apply_wjs_settings: Callable,  # noqa: ARG001
):
    """
    Generation of DOI for JCOM respects the specs.

    Here we test how DOIs are generated when looking only inside the system, i.e. without relying on any data from
    wjapp.

    """
    section = section_factory(name="any", journal=journal)
    section.refresh_from_db()
    section.wjssection.pubid_and_tex_sectioncode = JCOM_SECTION_TO_PUBIDSECTIONCODE[section_name]
    section.wjssection.save()

    issue = issue_factory(
        journal=journal,
        volume=volume,
        issue=issue,
    )
    for index in range(num_published_siblings):
        a = article_factory(
            title=f"Already published {section_name} - {index}",
            journal=journal,
            section=section,
            primary_issue=issue,
            date_published=timezone.now(),
        )
        a.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
        a.articleworkflow.save()

    article = article_factory(
        journal=journal,
        section=section,
    )
    assert article.page_numbers is None

    issue.articles.add(article)
    article.refresh_from_db()
    assert article.primary_issue == issue

    time_when_doi_is_generated = timezone.localtime(timezone.now())
    with freezegun.freeze_time(time_when_doi_is_generated):
        generated_doi = get_dois_for_articles([article], create=True)[0].identifier
    doi_prefix = setting_handler.get_setting("Identifiers", "crossref_prefix", article.journal).value
    expected_doi = (
        f"{doi_prefix}/{article.id}{timezone.localtime(time_when_doi_is_generated).strftime('%Y%m%d%H%M%S')}"
    )
    assert generated_doi == expected_doi

    assert article.articleworkflow.compute_pubid() == expected_pubid

    # TODO: might want to test that the machinery that sets the identifiers works


@pytest.mark.django_db
def test_doi_generation_jcom__conference_and_book_review(
    journal: Journal,
    jcom_doi_prefix: Callable,  # noqa: ARG001
    article_factory: Callable,
    section_factory: Callable,
    issue_factory: Callable,
    apply_wjs_settings: Callable,  # noqa: ARG001
):
    """
    Generation of pubid for JCOM conference and book review.

    They share the same counter (!?!)
    """
    # Set the stage: an issue with two book reviews and a conference review already published
    issue = issue_factory(journal=journal, volume=1, issue="02")

    section_name = "book review"
    bookreview_section = section_factory(name=section_name, journal=journal)
    bookreview_section.refresh_from_db()
    bookreview_section.wjssection.pubid_and_tex_sectioncode = JCOM_SECTION_TO_PUBIDSECTIONCODE[section_name]
    bookreview_section.wjssection.save()

    section_name = "conference review"
    conferencereview_section = section_factory(name=section_name, journal=journal)
    conferencereview_section.refresh_from_db()
    conferencereview_section.wjssection.pubid_and_tex_sectioncode = JCOM_SECTION_TO_PUBIDSECTIONCODE[section_name]
    conferencereview_section.wjssection.save()
    br1 = article_factory(
        title="Book review 1",
        journal=journal,
        section=bookreview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )
    br1.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    br1.articleworkflow.save()
    br2 = article_factory(
        title="Book review 2",
        journal=journal,
        section=bookreview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )
    br2.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    br2.articleworkflow.save()
    cr1 = article_factory(
        title="Conference review 1",
        journal=journal,
        section=conferencereview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )
    cr1.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    cr1.articleworkflow.save()

    # Now the test: new book or conference reviews should get counter == 4
    # I could use one of the articles created above,
    # but I prefer to simulate a scenario closer to the most common situation
    br3 = article_factory(
        journal=journal,
        section=bookreview_section,
        primary_issue=issue,
        date_published=timezone.now(),
    )

    time_when_doi_is_generated = timezone.localtime(timezone.now())
    with freezegun.freeze_time(time_when_doi_is_generated):
        generated_doi = get_dois_for_articles([br3], create=True)[0].identifier
    doi_prefix = setting_handler.get_setting("Identifiers", "crossref_prefix", br3.journal).value
    expected_doi = f"{doi_prefix}/{br3.id}{timezone.localtime(time_when_doi_is_generated).strftime('%Y%m%d%H%M%S')}"
    assert generated_doi == expected_doi
    assert br3.articleworkflow.compute_pubid() == f"JCOM_0102_{year}_R04"

    cr = article_factory(
        journal=journal,
        section=conferencereview_section,
        primary_issue=issue,
    )
    with freezegun.freeze_time(time_when_doi_is_generated):
        generated_doi = get_dois_for_articles([cr], create=True)[0].identifier
    expected_doi = f"{doi_prefix}/{cr.id}{timezone.localtime(time_when_doi_is_generated).strftime('%Y%m%d%H%M%S')}"
    assert generated_doi == expected_doi
    assert cr.articleworkflow.compute_pubid() == f"JCOM_0102_{year}_R04"
