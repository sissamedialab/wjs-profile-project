"""Test that the generation of the eids respects the specs of every journal.

These tests are intentionally "light": they create only what `ArticleWorkflow.compute_eid()` needs, i.e. a journal, an
issue, some sections and some articles. No users, no issue types, no galleys, ... and Janeway's default settings are
installed only once per session (they are needed to save journals and articles, but no test here touches them).
"""

import datetime

import pytest
from django.utils import timezone
from identifiers.models import Identifier
from journal.models import Issue, Journal
from plugins.wjs_review.models import ArticleWorkflow
from submission.models import Article, FrozenAuthor, Section
from utils.install import update_settings, update_xsl_files

from wjs.jcom_profile.templatetags.wjs_tags import how_to_cite

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


# The publication year of the articles whose eid is computed (it matters for JCAP, see JCAP_JQUANT_STYLE_SINCE).
DEFAULT_YEAR = 2026


class EidScenario:
    """A journal with one issue, where papers can be published and eids computed."""

    def __init__(self, journal_code: str, year: int = DEFAULT_YEAR):
        # bulk_create() to skip Journal's post_save signals (licenses, submission items, default review form, ...):
        # they are slow and irrelevant here
        self.journal = Journal.objects.bulk_create(
            [Journal(code=journal_code, domain=f"{journal_code.lower()}.testserver.org")],
        )[0]
        self.year = year
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
        """Create an unpublished article in the given section.

        The article already has a publication date (in the scenario's year), as during the publication process.
        """
        return Article.objects.create(
            journal=self.journal,
            title=f"A {section_name} in {self.journal.code}",
            section=self.section(section_name),
            primary_issue=self.issue,
            date_published=timezone.make_aware(datetime.datetime(self.year, 6, 1)),
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
    # JQuant: the eid has the form <issue><counter> (no section code), and the counter is per-issue
    ("JQuant", "Article", (), "001001"),
    ("JQuant", "Article", ("Article",), "001002"),
    ("JQuant", "Article", ("Erratum",), "001002"),
    ("JQuant", "Erratum", ("Article",), "001002"),
    # JSTAT: same as JQuant
    ("JSTAT", "Article", (), "001001"),
    ("JSTAT", "Erratum", ("Article", "Erratum"), "001003"),
    # JCAP (until 2026): "normal" papers get a plain counter (errata and addenda are not counted)...
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
    # JINST: same as JQuant
    ("JINST", "Article", (), "001001"),
    ("JINST", "Article", ("Erratum",), "001002"),
    ("JINST", "Erratum", ("Article",), "001002"),
    ("JINST", "Erratum", ("Erratum",), "001002"),
    ("JINST", "Addendum", ("Erratum",), "001002"),
)

# JCAP since 2027: same as JQuant (errata and addenda are counted together with the other papers)
JCAP_2027_CASES = (
    ("JCAP", "Paper", (), "001001"),
    ("JCAP", "Paper", ("Erratum",), "001002"),
    ("JCAP", "Erratum", ("Paper", "Paper"), "001003"),
    ("JCAP", "Addendum", ("Addendum",), "001002"),
)


@pytest.mark.parametrize(
    ("journal_code", "section_name", "already_published", "expected_eid", "year"),
    [(*case, DEFAULT_YEAR) for case in CASES] + [(*case, 2027) for case in JCAP_2027_CASES],
)
@pytest.mark.django_db
def test_compute_eid(
    journal_code: str,
    section_name: str,
    already_published: tuple[str, ...],
    expected_eid: str,
    year: int,
):
    """The eid of a paper depends on the journal, on the section and on the papers already published in the issue."""
    scenario = EidScenario(journal_code, year=year)
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
    """We don't know how to compute the eid of journals that we don't publish."""
    scenario = EidScenario("XYZ")
    article = scenario.article("Article")
    with pytest.raises(NotImplementedError, match="XYZ"):
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
def test_compute_eid__jcap_full_issue_since_2027():
    """Since 2027, the same JCAP issue gets eids 001001 001002 001003 001004 001005 001006."""
    scenario = EidScenario("JCAP", year=2027)
    expected_eids = ["001001", "001002", "001003", "001004", "001005", "001006"]
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


# (journal code, publication year, volume, section of the article, does the section have a code?, sections of the
# papers already published in the issue, expected pubid)
PUBID_CASES = (
    ("JQuant", 2026, 1, "Article", False, (), "JQUANT1(2026)002001"),
    ("JQuant", 2026, 1, "Article", False, ("Article", "Erratum"), "JQUANT1(2026)002003"),
    ("JQuant", 2026, 1, "Erratum", True, ("Article",), "JQUANT1(2026)002002"),
    ("JQuant", 2026, 1, "Article", True, (), "JQUANT1(2026)002001"),
    # JINST and JSTAT: same as JQuant
    ("JINST", 2026, 1, "Addendum", True, ("Paper", "Erratum"), "JINST1(2026)002003"),
    ("JSTAT", 2026, 1, "Paper", False, (), "JSTAT1(2026)002001"),
    # JCAP: same as JHEP until 2026 (the volume is the year)...
    ("JCAP", 2026, 2026, "Paper", False, ("Paper",), "JCAP02(2026)002"),
    ("JCAP", 2026, 2026, "Erratum", True, ("Paper", "Erratum"), "JCAP02(2026)E02"),
    # ...and same as JQuant since 2027
    ("JCAP", 2027, 1, "Paper", False, ("Paper",), "JCAP1(2027)002002"),
    ("JCAP", 2027, 1, "Erratum", True, ("Paper", "Erratum"), "JCAP1(2027)002003"),
    ("JHEP", 2026, 2026, "Paper", False, ("Paper", "Erratum"), "JHEP02(2026)003"),
    ("JCOM", 2026, 1, "Article", True, ("Article", "Paper"), "JCOM_0102_2026_A02"),
)


@pytest.mark.parametrize(
    ("journal_code", "year", "volume", "section_name", "has_code", "already_published", "expected"),
    PUBID_CASES,
)
@pytest.mark.django_db
def test_compute_pubid(
    journal_code: str,
    year: int,
    volume: int,
    section_name: str,
    has_code: bool,
    already_published: tuple[str, ...],
    expected: str,
):
    """The form of the pubid depends on the journal (and, for JCAP, on the publication year)."""
    scenario = EidScenario(journal_code, year=year)
    scenario.issue.volume = volume
    scenario.issue.issue = "2"
    scenario.issue.save()
    for published_section_name in already_published:
        scenario.publish(published_section_name)

    article = scenario.article(section_name)
    if not has_code:
        article.section.wjssection.pubid_and_tex_sectioncode = None
        article.section.wjssection.save()
    assert article.articleworkflow.compute_pubid() == expected


@pytest.mark.django_db
def test_compute_pubid__existing_page_numbers():
    """If the article already has a page number, that is the eid used in the pubid."""
    scenario = EidScenario("JQuant")
    article = scenario.article("Article")
    article.page_numbers = "005007"
    article.save()
    assert article.articleworkflow.compute_pubid() == "JQUANT1(2026)005007"


# (journal code, publication year, section of the article, sections of the papers already published in the issue,
# expected counter)
COUNTER_CASES = (
    # JCOM counts per-issue and per-section, with book and conference reviews counted together
    ("JCOM", 2026, "Article", ("Article", "Paper"), "02"),
    ("JCOM", 2026, "Book Review", ("Conference Review", "Article"), "02"),
    # JCOMAL counts per-issue and per-section
    ("JCOMAL", 2026, "Book Review", ("Conference Review", "Book Review"), "02"),
    # JHEP counts per-issue
    ("JHEP", 2026, "Erratum", ("Paper", "Paper"), "003"),
    # JCAP until 2026 counts per-issue, but errata and addenda are counted separately and per-section
    ("JCAP", 2026, "Paper", ("Paper", "Erratum", "Addendum"), "002"),
    ("JCAP", 2026, "Erratum", ("Paper", "Erratum", "Addendum"), "02"),
    # JCAP since 2027, JQuant, JINST and JSTAT count per-issue
    ("JCAP", 2027, "Erratum", ("Paper", "Erratum", "Addendum"), "004"),
    ("JQuant", 2026, "Erratum", ("Article", "Erratum"), "003"),
    ("JINST", 2026, "Article", ("Erratum",), "002"),
    ("JSTAT", 2026, "Article", (), "001"),
)


@pytest.mark.parametrize(("journal_code", "year", "section_name", "already_published", "expected"), COUNTER_CASES)
@pytest.mark.django_db
def test_collect_publication_data__counter(
    journal_code: str,
    year: int,
    section_name: str,
    already_published: tuple[str, ...],
    expected: str,
):
    """The counter is the position of the paper in its issue (and section), following the quirks of each journal."""
    scenario = EidScenario(journal_code, year=year)
    for published_section_name in already_published:
        scenario.publish(published_section_name)

    article = scenario.article(section_name)
    assert article.articleworkflow.collect_publication_data()["counter"] == expected


@pytest.mark.django_db
def test_collect_publication_data__counter_with_existing_page_numbers():
    """The counter is computed even if the article already has a page number (i.e. an eid)."""
    scenario = EidScenario("JQuant")
    scenario.publish("Article")
    article = scenario.article("Article")
    article.page_numbers = "005007"
    article.save()
    data = article.articleworkflow.collect_publication_data()
    assert (data["eid"], data["counter"]) == ("005007", "002")


@pytest.mark.django_db
def test_how_to_cite__jquant():
    """In JQuant the how-to-cite has the volume (years since inception), the publication year and the eid.

    The issue number is not repeated: it is already part of the eid (see wjs/specs#3213).
    """
    scenario = EidScenario("JQuant")
    issues = {
        (1, "01"): scenario.issue,
        (1, "02"): Issue.objects.create(journal=scenario.journal, volume=1, issue="02"),
        (2, "04"): Issue.objects.create(journal=scenario.journal, volume=2, issue="04"),
    }
    # (volume, issue, publication year, expected how-to-cite piece), in order of publication
    cases = (
        (1, "01", 2026, "<b>1</b> (2026) 001001"),
        (1, "01", 2026, "<b>1</b> (2026) 001002"),
        (1, "02", 2026, "<b>1</b> (2026) 002001"),
        (2, "04", 2027, "<b>2</b> (2027) 004001"),
    )

    for volume, issue_number, year, expected_piece in cases:
        article = Article.objects.create(
            journal=scenario.journal,
            title="TITLE",
            section=scenario.section("Article"),
            primary_issue=issues[(volume, issue_number)],
            date_published=timezone.make_aware(datetime.datetime(year, 6, 1)),
        )
        FrozenAuthor.objects.create(article=article, first_name="Mario", last_name="Rossi", order=1)
        Identifier.objects.create(id_type="doi", identifier="10.1234/test", article=article)
        article.articleworkflow.compute_eid(save_as_pagenumber=True)

        expected = f"M. Rossi, <i>TITLE</i>, <i>JQUANT</i> {expected_piece}, doi:10.1234/test"
        assert how_to_cite(article) == expected, f"Wrong how-to-cite for volume {volume} issue {issue_number}"
        # Publish the article only now, since _publish() sets the publication date to today
        _publish(article)
