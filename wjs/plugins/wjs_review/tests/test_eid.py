"""Test that the generation of the eids respects the specs of every journal.

These tests are intentionally "light": they create only what `ArticleWorkflow.compute_eid()` needs, i.e. a journal, an
issue, some sections and some articles. No users, no issue types, no galleys, ... and Janeway's default settings are
installed only once per session (they are needed to save journals and articles, but no test here touches them).
"""

import pytest
from django.utils import timezone
from journal.models import Issue, Journal
from plugins.wjs_review.models import ArticleWorkflow
from submission.models import Article, Section
from utils.install import update_settings, update_xsl_files

# The section code (i.e. the letter that might appear in the eid) of the sections used in these tests.
# In real life these come from the DB (WjsSection) and are set by the EO from the admin interface.
SECTION_CODES = {
    "Article": "A",
    "Paper": "P",
    "Book Review": "R",  # Same code for conference and book review
    "Conference Review": "R",
    "Erratum": "E",
    "Addendum": "A",
}


@pytest.fixture(autouse=True, scope="session")
def _janeway_defaults(django_db_setup, django_db_blocker):
    """
    Install the bare minimum that journals and articles expect to find (the default XSL file and the settings).

    Done once per session (outside the per-test transaction), because it takes ~1s and no test here modifies it.
    """
    with django_db_blocker.unblock():
        update_xsl_files()
        update_settings()


def _make_section(journal: Journal, name: str) -> Section:
    """Create a section (and its WjsSection, via signal) with the section code that it has in real life."""
    section = Section.objects.create(journal=journal, name=name)
    section.refresh_from_db()
    section.wjssection.pubid_and_tex_sectioncode = SECTION_CODES[name]
    section.wjssection.save()
    return section


def _publish(article: Article) -> Article:
    """Mark the given article as published (this is all that compute_eid() looks at)."""
    article.date_published = timezone.now()
    article.save()
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    article.articleworkflow.save()
    return article


class EidScenario:
    """A journal with one issue, where papers can be published and eids computed."""

    def __init__(self, journal_code: str):
        # bulk_create() to skip Journal's post_save signals (licenses, submission items, default review form, ...):
        # they are slow and irrelevant here
        self.journal = Journal.objects.bulk_create(
            [Journal(code=journal_code, domain=f"{journal_code.lower()}.testserver.org")],
        )[0]
        self.issue = Issue.objects.create(journal=self.journal, volume=1, issue="01")
        self.sections: dict[str, Section] = {}
        if journal_code == "JCOM":
            # JCOM's book and conference reviews share the same counter,
            # and the code that computes the eid expects both sections to exist
            self.section("Book Review")
            self.section("Conference Review")

    def section(self, name: str) -> Section:
        """Return the section with the given name, creating it if necessary."""
        if name not in self.sections:
            self.sections[name] = _make_section(self.journal, name)
        return self.sections[name]

    def article(self, section_name: str) -> Article:
        """Create an unpublished article in the given section."""
        return Article.objects.create(
            journal=self.journal,
            title=f"A {section_name} in {self.journal.code}",
            section=self.section(section_name),
            primary_issue=self.issue,
        )

    def publish(self, section_name: str) -> Article:
        """Create an article in the given section and publish it (without computing any eid)."""
        return _publish(self.article(section_name))


# (journal code, section of the article, sections of the papers already published in the issue, expected eid)
CASES = (
    # JCOM: the eid has the form <section code><counter>, and the counter is per-issue and per-section
    ("JCOM", "Article", (), "A01"),
    ("JCOM", "Article", ("Article",), "A02"),
    ("JCOM", "Article", ("Paper",), "A01"),
    ("JCOM", "Article", ("Article", "Paper"), "A02"),
    ("JCOM", "Paper", (), "P01"),
    ("JCOM", "Paper", ("Paper",), "P02"),
    ("JCOM", "Book Review", (), "R01"),
    ("JCOM", "Book Review", ("Book Review",), "R02"),
    # ...but book and conference reviews share the same counter
    ("JCOM", "Book Review", ("Conference Review",), "R02"),
    ("JCOM", "Conference Review", ("Article",), "R01"),
    ("JCOM", "Conference Review", ("Book Review", "Conference Review"), "R03"),
    # JCOMAL: same as JCOM...
    ("JCOMAL", "Article", (), "A01"),
    ("JCOMAL", "Article", ("Article",), "A02"),
    ("JCOMAL", "Article", ("Paper",), "A01"),
    ("JCOMAL", "Book Review", ("Book Review",), "R02"),
    # ...but book and conference reviews do not share the counter
    ("JCOMAL", "Book Review", ("Conference Review",), "R01"),
    # JHEP: the eid is just a counter, per-issue and irrespective of the section
    ("JHEP", "Paper", (), "001"),
    ("JHEP", "Paper", ("Paper",), "002"),
    ("JHEP", "Paper", ("Erratum",), "002"),
    ("JHEP", "Erratum", (), "001"),
    ("JHEP", "Erratum", ("Paper", "Paper"), "003"),
    ("JHEP", "Addendum", ("Paper", "Erratum"), "003"),
    # JQuant: same as JHEP
    ("JQuant", "Article", (), "001"),
    ("JQuant", "Article", ("Article",), "002"),
    ("JQuant", "Article", ("Erratum",), "002"),
    ("JQuant", "Erratum", ("Article",), "002"),
    # JCAP: "normal" papers get a plain counter (errata and addenda are not counted)...
    ("JCAP", "Paper", (), "001"),
    ("JCAP", "Paper", ("Paper",), "002"),
    ("JCAP", "Paper", ("Article",), "002"),
    ("JCAP", "Paper", ("Erratum",), "001"),
    ("JCAP", "Paper", ("Paper", "Erratum", "Addendum"), "002"),
    # ...while errata and addenda get a lettered eid, with a per-section counter
    ("JCAP", "Erratum", (), "E01"),
    ("JCAP", "Erratum", ("Paper", "Paper", "Paper"), "E01"),
    ("JCAP", "Erratum", ("Erratum",), "E02"),
    ("JCAP", "Erratum", ("Addendum",), "E01"),
    ("JCAP", "Addendum", ("Paper", "Erratum"), "A01"),
    ("JCAP", "Addendum", ("Addendum",), "A02"),
    # JINST: same as JCAP
    ("JINST", "Article", (), "001"),
    ("JINST", "Article", ("Erratum",), "001"),
    ("JINST", "Erratum", ("Article",), "E01"),
    ("JINST", "Erratum", ("Erratum",), "E02"),
    ("JINST", "Addendum", ("Erratum",), "A01"),
)


@pytest.mark.parametrize(("journal_code", "section_name", "already_published", "expected_eid"), CASES)
@pytest.mark.django_db
def test_compute_eid(
    journal_code: str,
    section_name: str,
    already_published: tuple[str, ...],
    expected_eid: str,
):
    """The eid of a paper depends on the journal, on the section and on the papers already published in the issue."""
    scenario = EidScenario(journal_code)
    for published_section_name in already_published:
        scenario.publish(published_section_name)

    article = scenario.article(section_name)
    assert article.page_numbers is None
    assert article.articleworkflow.compute_eid() == expected_eid
    # compute_eid() is free of side effects unless explicitly requested
    article.refresh_from_db()
    assert article.page_numbers is None


@pytest.mark.django_db
def test_compute_eid__unknown_journal():
    """We don't know (yet) how to compute the eid of journals such as JSTAT."""
    scenario = EidScenario("JSTAT")
    article = scenario.article("Article")
    with pytest.raises(NotImplementedError, match="JSTAT"):
        article.articleworkflow.compute_eid()


@pytest.mark.django_db
def test_compute_eid__existing_page_numbers():
    """If the article already has a page number, that is the eid (whatever the journal)."""
    scenario = EidScenario("JCAP")
    article = scenario.article("Paper")
    article.page_numbers = "007"
    article.save()
    assert article.articleworkflow.compute_eid() == "007"


@pytest.mark.django_db
def test_compute_eid__jcap_full_issue():
    """In JCAP, an issue with 3 papers, 1 erratum and 2 addenda gets eids 001 002 003 E01 A01 A02."""
    scenario = EidScenario("JCAP")
    expected_eids = ["001", "002", "003", "E01", "A01", "A02"]
    section_names = ["Paper", "Paper", "Paper", "Erratum", "Addendum", "Addendum"]

    computed_eids = []
    for section_name in section_names:
        article = scenario.article(section_name)
        computed_eids.append(article.articleworkflow.compute_eid(save_as_pagenumber=True))
        _publish(article)

    assert computed_eids == expected_eids


@pytest.mark.django_db
def test_compute_eid__jhep_full_issue():
    """In JHEP, the same issue (3 papers, 1 erratum and 2 addenda) gets eids 001 ... 006."""
    scenario = EidScenario("JHEP")
    expected_eids = ["001", "002", "003", "004", "005", "006"]
    section_names = ["Paper", "Paper", "Paper", "Erratum", "Addendum", "Addendum"]

    computed_eids = []
    for section_name in section_names:
        article = scenario.article(section_name)
        computed_eids.append(article.articleworkflow.compute_eid(save_as_pagenumber=True))
        _publish(article)

    assert computed_eids == expected_eids
