"""
Tests for the ``metadata_export`` package (Article -> JCAP/EM-style metadata XML export).

Three layers, per the package's own architecture:
- ``formatters.py`` tests: pure Python, no DB, no Django model at all.
- ``mappers.py`` tests: one (or a couple) per mapper function, against factory-built model
  instances -- not exhaustive, a first draft per export_spec.md's own instructions.
- one template-rendering test that hand-builds a ``dto.py`` object tree with literal values (no
  DB, no factories) and asserts on the rendered XML, proving the template contract independently
  of any Django model.
"""

import dataclasses
import datetime
import io
import stat
import xml.etree.ElementTree as ET  # noqa: S405,N817 -- parses self-rendered XML, not untrusted input
import zipfile
from unittest import mock

import pytest
from core.files import save_file_to_article
from core.models import (
    ControlledAffiliation,
    Country,
    File,
    Location,
    Organization,
    OrganizationName,
)
from django.conf import settings
from django.core.files import File as DjangoFile
from django.template.loader import render_to_string
from django.utils.module_loading import import_string
from plugins.wjs_review.metadata_export import formatters, mappers, publishers, service
from plugins.wjs_review.metadata_export.dto import (
    AffiliationExportDTO,
    ArticleExportDTO,
    AuthorExportDTO,
    CustomFieldExportDTO,
    DateParts,
    FileExportDTO,
    FunderExportDTO,
    JournalExportDTO,
    KeywordExportDTO,
)
from plugins.wjs_review.models import ArticleWorkflow, EditorDecision
from plugins.wjs_submission.models import AccessMode
from review.models import ReviewRound
from submission.models import ArticleFunding, FrozenAuthor, Licence, Section
from utils import setting_handler

# --------------------------------------------------------------------------------------------- #
# formatters.py -- pure, no DB
# --------------------------------------------------------------------------------------------- #


def test_to_two_letter_language_code_known_code():
    assert formatters.to_two_letter_language_code("eng") == "en", "eng must map to the en alpha-2 code"


def test_to_two_letter_language_code_is_case_insensitive():
    assert formatters.to_two_letter_language_code("ENG") == "en", "lookup should not be case sensitive"


def test_to_two_letter_language_code_unknown_passes_through():
    assert formatters.to_two_letter_language_code("xxx") == "xxx", "unmapped codes must pass through unchanged"


def test_to_two_letter_language_code_empty_string():
    assert formatters.to_two_letter_language_code("") == "", "an empty code must stay empty, not error"


def test_format_date_dd_mon_yyyy_formats_with_no_leading_zero_lost():
    value = datetime.date(2026, 7, 2)
    assert formatters.format_date_dd_mon_yyyy(value) == "02-Jul-2026", "day must be zero-padded, month as Mon"


def test_format_date_dd_mon_yyyy_none_is_empty():
    assert formatters.format_date_dd_mon_yyyy(None) == "", "a missing date must format as an empty string"


def test_build_date_parts_has_no_leading_zeros():
    value = datetime.datetime(2026, 7, 2, 10, 30)
    parts = formatters.build_date_parts(value)
    assert (parts.year, parts.month, parts.day) == ("2026", "7", "2"), "no leading zeros on month/day"


def test_build_date_parts_none_is_empty_and_falsy():
    parts = formatters.build_date_parts(None)
    assert (parts.year, parts.month, parts.day) == ("", "", ""), "a missing date must build an all-empty DateParts"
    assert not parts, "an all-empty DateParts must be falsy"


def test_date_parts_is_truthy_when_year_is_set():
    assert DateParts(year="2026", month="7", day="2"), "a DateParts with a year must be truthy"


def test_to_single_line_collapses_newlines():
    assert formatters.to_single_line("line one\nline two\r\nline three") == "line one line two line three"


def test_to_single_line_collapses_repeated_whitespace():
    assert formatters.to_single_line("a   b\t\tc") == "a b c"


def test_to_single_line_strips_leading_and_trailing_whitespace():
    assert formatters.to_single_line("  \n  padded  \n  ") == "padded"


def test_to_single_line_empty_string_is_empty():
    assert formatters.to_single_line("") == ""


def test_to_single_line_leaves_a_single_line_unchanged():
    assert formatters.to_single_line("already one line.") == "already one line."


# --------------------------------------------------------------------------------------------- #
# mappers.py -- one (or a couple) test(s) per mapper function, against real model instances
# --------------------------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_map_language(article):
    article.language = "eng"
    assert mappers.map_language(article) == "en", "article.language (alpha-3) must convert to alpha-2"


@pytest.mark.django_db
def test_map_ms_no_uses_articleworkflow_preprint_id(article):
    assert mappers.map_ms_no(article) == article.articleworkflow.preprint_id


@pytest.mark.django_db
def test_map_ms_no_without_workflow_is_empty(article):
    article.articleworkflow.delete()
    article.refresh_from_db()
    assert mappers.map_ms_no(article) == "", "no ArticleWorkflow row must map to an empty ms_no, not an error"


@pytest.mark.django_db
def test_map_rev_uses_the_default_per_journal_value(article):
    assert mappers.map_rev(article) == "2", "settled default (no per-journal override defined yet)"


@pytest.mark.django_db
def test_map_rev_uses_a_per_journal_override_when_present(article, monkeypatch):
    monkeypatch.setitem(mappers.EXPORT_REV_BY_JOURNAL_CODE, article.journal.code, "9")
    assert mappers.map_rev(article) == "9"


@pytest.mark.django_db
def test_map_rev_id_with_no_major_revision_is_zero(article):
    assert mappers.map_rev_id(article) == 0


@pytest.mark.django_db
def test_map_rev_id_counts_the_articles_major_revision_decisions(assigned_article, editor_revision):
    decision = (
        assigned_article.articleworkflow.decisions.filter(decision=ArticleWorkflow.Decisions.MAJOR_REVISION)
        .order_by("-created")
        .first()
    )
    assert decision is not None, "editor_revision must have created a major-revision EditorDecision"

    assert mappers.map_rev_id(assigned_article) == 1, "the article's first (and only) major revision"


@pytest.mark.django_db
def test_map_rev_id_is_not_inflated_by_an_intervening_minor_revision(article, editor):
    """A minor revision before a major one must not inflate rev_id via review_round.round_number.

    Regression for MR !1487 review: rev_id used to be `review_round.round_number` of the newest
    major-revision EditorDecision, which over-counts whenever a minor/technical revision round
    falls between two major-revision rounds.
    """
    minor_round = ReviewRound.objects.create(article=article, round_number=1)
    EditorDecision.objects.create(
        workflow=article.articleworkflow,
        editor=editor,
        review_round=minor_round,
        decision=ArticleWorkflow.Decisions.MINOR_REVISION,
    )
    major_round = ReviewRound.objects.create(article=article, round_number=2)
    EditorDecision.objects.create(
        workflow=article.articleworkflow,
        editor=editor,
        review_round=major_round,
        decision=ArticleWorkflow.Decisions.MAJOR_REVISION,
    )

    assert mappers.map_rev_id(article) == 1, "this is the article's first major revision, despite being in round 2"


@pytest.mark.django_db
def test_map_journal(article):
    setting_handler.save_setting("general", "publisher_name", article.journal, "Test Publisher")
    setting_handler.save_setting("general", "journal_name", article.journal, "Test Journal")
    setting_handler.save_setting("general", "journal_issn", article.journal, "1234-5678")
    setting_handler.save_setting("general", "print_issn", article.journal, "0001-0002")

    result = mappers.map_journal(article)

    assert result == JournalExportDTO(
        publisher_name="Test Publisher",
        full_journal_title="Test Journal",
        journal_abbreviation=article.journal.code,
        issn_print="0001-0002",
        issn_digital="1234-5678",
    ), "journal fields must come from the journal's settings-backed properties + code field"


@pytest.mark.django_db
def test_map_decision_status_accepted(article):
    article.stage = "Accepted"
    assert mappers.map_decision_status(article) == "accept"


@pytest.mark.django_db
def test_map_decision_label_accepted(article):
    article.stage = "Accepted"
    assert mappers.map_decision_label(article) == "Accepted"


@pytest.mark.django_db
@pytest.mark.parametrize("stage", ["Submitted", "Rejected", "Under Review"])
def test_map_decision_status_raises_for_unsupported_stage(article, stage):
    """Settled: this export only fires for accepted articles; any other stage raises."""
    article.stage = stage
    with pytest.raises(mappers.UnsupportedArticleStageForExportError):
        mappers.map_decision_status(article)


@pytest.mark.django_db
@pytest.mark.parametrize("stage", ["Submitted", "Rejected", "Under Review"])
def test_map_decision_label_raises_for_unsupported_stage(article, stage):
    article.stage = stage
    with pytest.raises(mappers.UnsupportedArticleStageForExportError):
        mappers.map_decision_label(article)


def _make_organization_with_location(name: str, city: str, country: Country) -> Organization:
    organization = Organization.objects.create()
    OrganizationName.objects.create(custom_label_for=organization, value=name)
    location = Location.objects.create(name=city, country=country)
    organization.locations.add(location)
    return organization


@pytest.mark.django_db
def test_map_affiliation_prefers_the_primary_one(article, author, country):
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    secondary_org = _make_organization_with_location("Secondary Org", "Elsewhere", country)
    primary_org = _make_organization_with_location("Primary Org", "Trieste", country)
    ControlledAffiliation.objects.create(
        frozen_author=frozen_author,
        organization=secondary_org,
        is_primary=False,
        department="Dept B",
    )
    ControlledAffiliation.objects.create(
        frozen_author=frozen_author,
        organization=primary_org,
        is_primary=True,
        department="Dept A",
        title="Prof",
    )

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Primary Org", "the is_primary=True affiliation must win over others"
    assert result.department == "Dept A"
    assert result.person_title == "Prof"
    assert result.city == "Trieste"
    assert result.country == country.name


@pytest.mark.django_db
def test_map_affiliation_falls_back_to_first_when_no_primary(article, author, country):
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    only_org = _make_organization_with_location("Only Org", "Somewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=only_org, is_primary=False)

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Only Org", "with no is_primary=True row, the first one must be used"


@pytest.mark.django_db
def test_map_affiliation_with_no_controlled_affiliation_is_empty(article, author):
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)

    assert (
        mappers.map_affiliation(frozen_author, article) == AffiliationExportDTO()
    ), "an author with no ControlledAffiliation at all must map to an all-empty affiliation"


@pytest.mark.django_db
def test_map_affiliation_uses_submission_data_affiliation_for_ta_corresponding_author(article, author, country):
    """IOP feedback: for a TA article, the corresponding author's exported institution is the TA-eligible one."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=2)
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    ta_org = _make_organization_with_location("TA-Eligible Org", "Trieste", country)
    ta_affiliation = ControlledAffiliation.objects.create(organization=ta_org)
    access_mode = AccessMode.objects.create(name="TA", code="oa-transformative-agreement")
    article.submission_data.access_mode = access_mode
    article.submission_data.affiliation = ta_affiliation
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "TA-Eligible Org", "must use the TA-eligible institution, not the author's own"


@pytest.mark.django_db
def test_map_affiliation_ignores_submission_data_affiliation_for_non_corresponding_author(
    article,
    author,
    coauthor,
    country,
):
    """The TA override only applies to the corresponding author -- everyone else keeps their own affiliation."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=coauthor, order=1)
    # The `article` fixture already sets correspondence_author=author; coauthor is NOT corresponding.
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    ta_org = _make_organization_with_location("TA-Eligible Org", "Trieste", country)
    ta_affiliation = ControlledAffiliation.objects.create(organization=ta_org)
    access_mode = AccessMode.objects.create(name="TA", code="oa-transformative-agreement")
    article.submission_data.access_mode = access_mode
    article.submission_data.affiliation = ta_affiliation
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Author's Own Org"


@pytest.mark.django_db
def test_map_affiliation_ignores_submission_data_affiliation_when_not_ta(article, author, country):
    """A plain (non-TA) OA-agreed article does not trigger the TA institution override."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    ta_org = _make_organization_with_location("TA-Eligible Org", "Trieste", country)
    ta_affiliation = ControlledAffiliation.objects.create(organization=ta_org)
    access_mode = AccessMode.objects.create(name="Open Access", code="open-access")
    article.submission_data.access_mode = access_mode
    article.submission_data.affiliation = ta_affiliation
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Author's Own Org", "only the oa-transformative-agreement code triggers the override"


@pytest.mark.django_db
def test_map_affiliation_falls_back_to_own_when_submission_data_affiliation_unset(article, author, country):
    """A TA article whose submission_data.affiliation is unset falls back to the author's own affiliation."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    article.correspondence_author = author
    own_org = _make_organization_with_location("Author's Own Org", "Elsewhere", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=own_org, is_primary=True)

    access_mode = AccessMode.objects.create(name="TA", code="oa-transformative-agreement")
    article.submission_data.access_mode = access_mode
    # article.submission_data.affiliation deliberately left unset (None).
    article.submission_data.save()

    result = mappers.map_affiliation(frozen_author, article)

    assert result.institution == "Author's Own Org"


@pytest.mark.django_db
def test_corresponding_author_affiliation_is_incomplete_when_no_correspondence_author(article):
    article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=article, order=1, first_name="Solo")
    article.correspondence_author = None
    article.save()

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is True


@pytest.mark.django_db
def test_corresponding_author_affiliation_is_incomplete_when_no_correspondence_author_even_with_an_unlinked_complete_affiliation(  # noqa: E501
    article, country
):
    """
    An unlinked FrozenAuthor with a complete affiliation must not be mistaken for the missing
    correspondence author.
    """
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, order=1, first_name="Solo")
    article.correspondence_author = None
    article.save()
    org = _make_organization_with_location("Unlinked Org", "Trieste", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=org, is_primary=True)

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is True


@pytest.mark.django_db
def test_corresponding_author_affiliation_is_incomplete_with_bare_controlled_affiliation_row(article, author):
    """A ControlledAffiliation row can exist with no organization at all -- organization is nullable."""
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=2, first_name="Corr")
    article.correspondence_author = author
    ControlledAffiliation.objects.create(frozen_author=frozen_author, is_primary=True)  # no organization

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is True


@pytest.mark.django_db
def test_corresponding_author_affiliation_is_complete_when_inst_city_country_all_present(article, author, country):
    article.frozenauthor_set.all().delete()
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)
    article.correspondence_author = author
    org = _make_organization_with_location("Complete Org", "Trieste", country)
    ControlledAffiliation.objects.create(frozen_author=frozen_author, organization=org, is_primary=True)

    assert mappers.corresponding_author_affiliation_is_incomplete(article) is False


@pytest.mark.django_db
def test_map_author_is_corresponding_when_matching_correspondence_author(article, author):
    frozen_author = FrozenAuthor.objects.create(
        article=article,
        author=author,
        order=1,
        first_name="Jane",
        last_name="Doe",
    )
    article.correspondence_author = author

    result = mappers.map_author(frozen_author, article)

    assert result.is_corresponding is True
    assert result.user_id == str(frozen_author.pk), "settled: user_id is always FrozenAuthor.pk, never Account.pk"


@pytest.mark.django_db
def test_map_author_is_not_corresponding_for_a_different_author(article, author, coauthor):
    frozen_author = FrozenAuthor.objects.create(article=article, author=coauthor, order=2)
    article.correspondence_author = author

    result = mappers.map_author(frozen_author, article)

    assert result.is_corresponding is False


@pytest.mark.django_db
def test_map_author_uses_frozen_email_when_set(article, author):
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1, frozen_email="frozen@x.org")

    assert mappers.map_author(frozen_author, article).email == "frozen@x.org"


@pytest.mark.django_db
def test_map_author_falls_back_to_linked_account_email(article, author):
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1, frozen_email="")

    assert (
        mappers.map_author(frozen_author, article).email == author.email
    ), "an empty frozen_email with a linked account must fall back to the account's email"


@pytest.mark.django_db
def test_map_author_email_is_empty_string_when_neither_frozen_email_nor_account(article):
    """Neither frozen_email nor a linked account: email must be "", not None (renders as literal "None" in XML)."""
    frozen_author = FrozenAuthor.objects.create(article=article, author=None, order=1, frozen_email="")

    assert mappers.map_author(frozen_author, article).email == ""


@pytest.mark.django_db
def test_map_author_salutation_uses_name_prefix_when_set(article):
    frozen_author = FrozenAuthor.objects.create(article=article, order=1, name_prefix="Prof.")

    assert mappers.map_author(frozen_author, article).salutation == "Prof."


@pytest.mark.django_db
def test_map_author_salutation_defaults_to_dr_when_name_prefix_is_empty(article):
    frozen_author = FrozenAuthor.objects.create(article=article, order=1, name_prefix="")

    assert mappers.map_author(frozen_author, article).salutation == "Dr."


@pytest.mark.django_db
def test_map_author_user_id_is_frozen_author_pk_even_when_linked(article, author):
    frozen_author = FrozenAuthor.objects.create(article=article, author=author, order=1)

    assert mappers.map_author(frozen_author, article).user_id == str(frozen_author.pk)
    assert frozen_author.pk != author.pk, "sanity check: these must be different objects/ids"


@pytest.mark.django_db
def test_map_author_user_id_is_frozen_author_pk_when_unlinked(article):
    frozen_author = FrozenAuthor.objects.create(article=article, author=None, order=1, first_name="Anon")

    assert mappers.map_author(frozen_author, article).user_id == str(frozen_author.pk)


@pytest.mark.django_db
def test_map_authors_is_ordered_by_frozen_author_order(article):
    article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=article, order=3, first_name="Third")
    FrozenAuthor.objects.create(article=article, order=1, first_name="First")
    FrozenAuthor.objects.create(article=article, order=2, first_name="Second")

    authors = mappers.map_authors(article)

    assert [a.first_name for a in authors] == ["First", "Second", "Third"]
    assert [a.seq for a in authors] == [1, 2, 3]


@pytest.mark.django_db
def test_map_authors_seq_is_consecutive_even_when_order_has_gaps(article):
    """IOP feedback: @author_seq must be consecutive; FrozenAuthor.order can have gaps."""
    article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=article, order=9, first_name="Third")
    FrozenAuthor.objects.create(article=article, order=1, first_name="First")
    FrozenAuthor.objects.create(article=article, order=5, first_name="Second")

    authors = mappers.map_authors(article)

    assert [a.first_name for a in authors] == ["First", "Second", "Third"], "ordering still follows .order"
    assert [a.seq for a in authors] == [1, 2, 3], "but @author_seq must be the 1-indexed position, not .order"


@pytest.mark.django_db
def test_map_authors_empty_list_for_article_with_no_authors(article):
    article.frozenauthor_set.all().delete()

    assert mappers.map_authors(article) == []


@pytest.mark.django_db
def test_map_pii(article):
    assert mappers.map_pii(article) == article.journal.code


@pytest.mark.django_db
def test_map_title(article):
    assert mappers.map_title(article) == article.title


@pytest.mark.django_db
def test_map_subtitle_none_is_empty_string(article):
    article.subtitle = None
    assert mappers.map_subtitle(article) == ""


@pytest.mark.django_db
def test_map_publication_type_section_name_none_is_empty_string(article):
    article.section = Section.objects.create(journal=article.journal, name=None)
    assert mappers.map_publication_type(article) == ""


@pytest.mark.django_db
def test_map_publication_type_no_section_is_empty_string(article):
    article.section = None
    assert mappers.map_publication_type(article) == ""


@pytest.mark.django_db
def test_map_abstract_collapses_line_breaks(article):
    article.abstract = "First paragraph.\n\nSecond paragraph,\r\nwrapped across two lines."
    assert mappers.map_abstract(article) == "First paragraph. Second paragraph, wrapped across two lines."


@pytest.mark.django_db
def test_map_files_groups_manuscript_and_source_files(article):
    manuscript_file = File.objects.create(original_filename="article.pdf")
    article.manuscript_files.set([manuscript_file])
    source_file = File.objects.create(original_filename="manuscript.TEX")
    article.source_files.add(source_file)

    files = mappers.map_files(article)

    assert files == [
        FileExportDTO(
            designation="Complete Document for Review (PDF Only)",
            file_name="article.pdf",
            file_format="pdf",
        ),
        FileExportDTO(
            designation="Source Files (incl. Word, TeX, Figures, etc)",
            file_name="manuscript.TEX",
            file_format="tex",
        ),
    ], "manuscript comes from manuscript_files (not galley_set); format is derived from the extension"


@pytest.mark.django_db
def test_get_export_files_returns_manuscript_then_source_files(article):
    """map_files and the production export zip builder must agree on "which files are linked"."""
    manuscript_file = File.objects.create(original_filename="article.pdf")
    article.manuscript_files.set([manuscript_file])
    source_file = File.objects.create(original_filename="manuscript.TEX")
    article.source_files.add(source_file)

    manuscript_files, source_files = mappers.get_export_files(article)

    assert manuscript_files == [manuscript_file]
    assert source_files == [source_file]


@pytest.mark.django_db
def test_map_files_excludes_supplementary_files(article):
    """The ``article`` fixture pre-populates supplementary_files; map_files must ignore them."""
    article.manuscript_files.clear()
    article.source_files.clear()
    assert article.supplementary_files.exists(), "sanity check: the fixture must still have supplementary files"

    assert mappers.map_files(article) == [], "supplementary_files (ESM) are not source files and must be dropped"


@pytest.mark.django_db
def test_map_received_date_uses_date_submitted(assigned_article):
    assert mappers.map_received_date(assigned_article) == formatters.build_date_parts(
        assigned_article.date_submitted,
    )


@pytest.mark.django_db
def test_map_received_date_empty_when_never_submitted(article):
    assert not mappers.map_received_date(article), "the article fixture has no date_submitted"


@pytest.mark.django_db
def test_map_revised_date_empty_with_no_major_revision(assigned_article):
    assert not mappers.map_revised_date(assigned_article)


@pytest.mark.django_db
def test_map_revised_date_uses_latest_major_revision_decision(assigned_article, editor_revision):
    major_revision_decision = (
        assigned_article.articleworkflow.decisions.filter(decision=ArticleWorkflow.Decisions.MAJOR_REVISION)
        .order_by("-created")
        .first()
    )
    assert major_revision_decision is not None, "editor_revision must have created a major-revision EditorDecision"

    expected = formatters.build_date_parts(major_revision_decision.modified or major_revision_decision.created)
    assert mappers.map_revised_date(assigned_article) == expected


@pytest.mark.django_db
def test_map_submitted_date_empty_with_no_major_revision(assigned_article):
    assert not mappers.map_submitted_date(assigned_article)


@pytest.mark.django_db
def test_map_submitted_date_empty_while_revision_in_progress(assigned_article, editor_revision):
    assert editor_revision.date_completed is None, "sanity check: the revision hasn't been submitted yet"

    assert not mappers.map_submitted_date(
        assigned_article,
    ), "settled: no fallback -- submitted_date stays empty until the revision is completed"


@pytest.mark.django_db
def test_map_submitted_date_uses_completed_major_revision_request(assigned_article, editor_revision):
    editor_revision.date_completed = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    editor_revision.save()

    assert mappers.map_submitted_date(assigned_article) == formatters.build_date_parts(editor_revision.date_completed)


@pytest.mark.django_db
def test_map_decision_date_empty_when_not_accepted(article):
    assert not mappers.map_decision_date(article), "no date_accepted must leave decision_date empty"


@pytest.mark.django_db
def test_map_decision_date_uses_article_date_accepted(accepted_article):
    """Simplified per MR !1487 review: article.date_accepted, not the accept EditorDecision's timestamp."""
    assert accepted_article.date_accepted is not None, "accepted_article must have gone through accept_article()"

    expected = formatters.build_date_parts(accepted_article.date_accepted)
    assert mappers.map_decision_date(accepted_article) == expected


@pytest.mark.django_db
def test_map_custom_fields_oa_not_agreed_by_default(article):
    fields = mappers.map_custom_fields(article)

    by_code = {f.code: f for f in fields}
    assert by_code["OA Agreed"].value == "No", "no submission_data at all must default to No"
    assert "OA date requested" not in by_code, "IOP feedback: omitted entirely, not just empty, when not agreed"
    assert "OA licence type" not in by_code, "IOP feedback: omitted entirely, not just empty, when not agreed"
    assert by_code["Copyright/Licence Type"].value == "Standard", "IOP's preset vocabulary, not license.short_name"


@pytest.mark.django_db
def test_map_custom_fields_oa_agreed_sets_date_and_is_yes(article):
    """ "OA date requested" uses the acceptance date, not the submission date."""
    access_mode = AccessMode.objects.create(name="Open Access", code="open-access")
    article.submission_data.access_mode = access_mode
    article.submission_data.save()
    article.date_submitted = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    article.date_accepted = datetime.datetime(2026, 7, 2, tzinfo=datetime.UTC)

    fields = mappers.map_custom_fields(article)
    by_code = {f.code: f for f in fields}

    assert by_code["OA Agreed"].value == "Yes"
    assert by_code["OA date requested"].value == "02-Jul-2026", "must be the acceptance date, not date_submitted"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "access_mode_code",
    ["open-access", "open-access-paid", "oa-transformative-agreement", "oa-cern", "oa-cern-affiliated"],
)
def test_map_custom_fields_oa_agreed_for_every_oa_flavored_access_mode(article, access_mode_code):
    """Every OA-flavored AccessMode code counts as OA agreed, not just the plain "open-access" one."""
    access_mode = AccessMode.objects.create(name=access_mode_code, code=access_mode_code)
    article.submission_data.access_mode = access_mode
    article.submission_data.save()

    fields = mappers.map_custom_fields(article)

    by_code = {f.code: f for f in fields}
    assert by_code["OA Agreed"].value == "Yes"


@pytest.mark.django_db
def test_map_custom_fields_licence_fields_when_oa_not_agreed(article):
    """IOP feedback: with OA not agreed, both OA-only fields are omitted and Copyright/Licence Type is "Standard"."""
    licence = Licence.objects.create(name="Creative Commons", short_name="CC BY 4.0", url="https://example.org")
    article.license = licence

    fields = mappers.map_custom_fields(article)
    by_code = {f.code: f for f in fields}

    assert "OA licence type" not in by_code
    assert "OA date requested" not in by_code
    assert (
        by_code["Copyright/Licence Type"].value == "Standard"
    ), "IOP's preset vocabulary only accepts Standard/Open Access -- never license.short_name"


@pytest.mark.django_db
def test_map_custom_fields_copyright_licence_type_is_open_access_when_oa_agreed(article):
    access_mode = AccessMode.objects.create(name="Open Access", code="open-access")
    article.submission_data.access_mode = access_mode
    article.submission_data.save()
    licence = Licence.objects.create(name="Creative Commons", short_name="CC BY 4.0", url="https://example.org")
    article.license = licence

    fields = mappers.map_custom_fields(article)
    by_code = {f.code: f for f in fields}

    assert by_code["OA licence type"].value == "CC BY 4.0", "OA licence type is unaffected by OA Agreed"
    assert (
        by_code["Copyright/Licence Type"].value == "Open Access"
    ), "settled: fixed literal when OA Agreed, no longer the same value as OA licence type"


@pytest.mark.django_db
def test_map_custom_fields_special_issue_when_not_a_collection(article):
    fields = mappers.map_custom_fields(article)
    special_issue_field = next(f for f in fields if f.code == "Special Issue")

    assert special_issue_field.name == "Special Issue Title"
    assert special_issue_field.value == ""


@pytest.mark.django_db
def test_map_custom_fields_special_issue_when_a_collection(special_issue, article):
    fields = mappers.map_custom_fields(article)
    special_issue_field = next(f for f in fields if f.code == "Special Issue")

    assert special_issue_field.name == special_issue.issue_title
    assert special_issue_field.value == special_issue.short_name


@pytest.mark.django_db
def test_map_custom_fields_never_emits_section_or_additional_authors(article):
    # Re-settled (IOP feedback): "Section" duplicated <publication_type>; "Additional Authors"
    # duplicated the individually-listed <author_list> entries. Neither is emitted any more.
    article.frozenauthor_set.all().delete()
    FrozenAuthor.objects.create(article=article, order=1, first_name="First", last_name="Author")
    FrozenAuthor.objects.create(article=article, order=2, first_name="Second", last_name="Author")

    fields = mappers.map_custom_fields(article)
    codes = {f.code for f in fields}

    assert "Section" not in codes
    assert "Additional Authors" not in codes


@pytest.mark.django_db
def test_map_custom_fields_produces_four_entries_in_order_when_oa_not_agreed(article):
    fields = mappers.map_custom_fields(article)

    assert [f.code for f in fields] == [
        "OA Agreed",
        "Copyright/Licence Type",
        "Production Comments",
        "Special Issue",
    ]


@pytest.mark.django_db
def test_map_custom_fields_produces_all_six_entries_in_order_when_oa_agreed(article):
    access_mode = AccessMode.objects.create(name="Open Access", code="open-access")
    article.submission_data.access_mode = access_mode
    article.submission_data.save()

    fields = mappers.map_custom_fields(article)

    assert [f.code for f in fields] == [
        "OA Agreed",
        "OA date requested",
        "OA licence type",
        "Copyright/Licence Type",
        "Production Comments",
        "Special Issue",
    ]


@pytest.mark.django_db
def test_map_keywords(article, keywords):
    selected = list(keywords[:2])
    article.keywords.add(*selected)

    result = mappers.map_keywords(article)

    assert {k.id for k in result} == {str(k.pk) for k in selected}
    assert {k.name for k in result} == {k.word for k in selected}


def test_count_manuscript_pages_returns_none_when_no_manuscript():
    fake_article = mock.Mock()
    fake_article.manuscript_files.first.return_value = None

    assert mappers._count_manuscript_pages(fake_article) is None


def test_count_manuscript_pages_returns_none_when_not_a_pdf():
    fake_manuscript = mock.Mock()
    fake_manuscript.self_article_path.return_value = "/tmp/fake.docx"
    fake_article = mock.Mock()
    fake_article.manuscript_files.first.return_value = fake_manuscript

    assert mappers._count_manuscript_pages(fake_article) is None


def test_count_manuscript_pages_parses_pdfinfo_output(monkeypatch):
    fake_manuscript = mock.Mock()
    fake_manuscript.self_article_path.return_value = "/tmp/fake.pdf"
    fake_article = mock.Mock()
    fake_article.manuscript_files.first.return_value = fake_manuscript
    fake_result = mock.Mock(stdout="Producer: X\nPages: 12\nSomething: Y\n")
    monkeypatch.setattr(mappers.subprocess, "run", mock.Mock(return_value=fake_result))

    assert mappers._count_manuscript_pages(fake_article) == 12


def test_count_manuscript_pages_returns_none_when_pdfinfo_fails(monkeypatch):
    fake_manuscript = mock.Mock()
    fake_manuscript.self_article_path.return_value = "/tmp/fake.pdf"
    fake_article = mock.Mock()
    fake_article.manuscript_files.first.return_value = fake_manuscript

    def _raise(*args, **kwargs):
        raise OSError("pdfinfo not found")

    monkeypatch.setattr(mappers.subprocess, "run", _raise)

    assert mappers._count_manuscript_pages(fake_article) is None


@pytest.mark.django_db
def test_map_total_pages_set(article):
    article.total_pages = 14
    assert mappers.map_total_pages(article) == "14"


@pytest.mark.django_db
def test_map_total_pages_does_not_recompute_when_already_set(article):
    article.total_pages = 5

    def _fail(a):
        raise AssertionError("must not attempt to compute pages when total_pages is already set")

    with mock.patch.object(mappers, "_count_manuscript_pages", _fail):
        assert mappers.map_total_pages(article) == "5"


@pytest.mark.django_db
def test_map_total_pages_unset_with_no_computable_page_count_is_empty(article):
    article.manuscript_files.clear()
    article.total_pages = None
    assert mappers.map_total_pages(article) == ""


@pytest.mark.django_db
def test_map_total_pages_computes_and_persists_when_unset(article):
    article.manuscript_files.set([File.objects.create(original_filename="article.pdf")])
    article.total_pages = None

    with mock.patch.object(mappers, "_count_manuscript_pages", return_value=12):
        result = mappers.map_total_pages(article)

    assert result == "12"
    article.refresh_from_db()
    assert article.total_pages == 12, "the computed page count must be persisted onto Article.total_pages"


@pytest.mark.django_db
def test_map_funders_list_with_no_funding_rows_returns_one_placeholder(article):
    assert mappers.map_funders_list(article) == [FunderExportDTO()]


@pytest.mark.django_db
def test_map_funders_list_with_funding_rows(article):
    ArticleFunding.objects.create(
        article=article,
        name="Some Funder",
        fundref_id="https://doi.org/10.1/x",
        funding_id="G-123",
    )

    assert mappers.map_funders_list(article) == [
        FunderExportDTO(name="Some Funder", fundref_id="https://doi.org/10.1/x", funding_id="G-123"),
    ]


@pytest.mark.django_db
def test_map_no_funders_true_when_no_funding_rows(article):
    assert mappers.map_no_funders(article) is True


@pytest.mark.django_db
def test_map_no_funders_false_with_funding_rows(article):
    ArticleFunding.objects.create(article=article, name="Some Funder")
    assert mappers.map_no_funders(article) is False


def test_jcap_fields_covers_every_article_export_dto_field():
    dto_field_names = {f.name for f in dataclasses.fields(ArticleExportDTO)}
    assert (
        set(mappers.JCAP_FIELDS.keys()) == dto_field_names
    ), "every ArticleExportDTO field must have exactly one mapper registered in JCAP_FIELDS"


def test_compose_dto_builds_dto_from_field_spec():
    field_spec = {"title": lambda article: "T", "subtitle": lambda article: "S"}

    dto = mappers.compose_dto(article=object(), dto_class=ArticleExportDTO, field_spec=field_spec)

    assert dto.title == "T"
    assert dto.subtitle == "S"


@pytest.mark.django_db
def test_build_article_export_dto_is_a_thin_composition(article):
    """Smoke test: build_article_export_dto must not blow up and must return a fully-typed DTO."""
    article.stage = "Accepted"
    dto = mappers.build_article_export_dto(article)

    assert isinstance(dto, ArticleExportDTO)
    assert dto.title == article.title
    assert dto.pii == article.journal.code
    assert len(dto.authors) == article.frozenauthor_set.count()


@pytest.mark.django_db
def test_build_article_export_dto_raises_for_non_accepted_article(article):
    article.stage = "Submitted"
    with pytest.raises(mappers.UnsupportedArticleStageForExportError):
        mappers.build_article_export_dto(article)


@pytest.mark.django_db
def test_build_article_export_dto_abstract_has_no_line_breaks(article):
    """<abstract> must be one-line text -- Article.abstract may contain literal newlines."""
    article.stage = "Accepted"
    article.abstract = "First paragraph.\n\nSecond paragraph,\r\nwrapped across two lines."
    article.save()

    dto = mappers.build_article_export_dto(article)

    assert "\n" not in dto.abstract
    assert "\r" not in dto.abstract
    assert dto.abstract == "First paragraph. Second paragraph, wrapped across two lines."


@pytest.mark.django_db
def test_build_article_export_dto_subtitle_none_is_empty_string(article):
    # `Article.subtitle` is `null=True` -- real articles can have `subtitle is None`. The
    # export must render an empty string, never the literal text "None" (export_spec.md).
    article.stage = "Accepted"
    article.subtitle = None
    dto = mappers.build_article_export_dto(article)
    assert dto.subtitle == ""


@pytest.mark.django_db
def test_build_article_export_dto_publication_type_section_name_none_is_empty_string(article):
    # See `test_map_publication_type_section_name_none_is_empty_string`: `Section.name` is
    # nullable even when a `Section` is set.
    article.stage = "Accepted"
    article.section = Section.objects.create(journal=article.journal, name=None)
    dto = mappers.build_article_export_dto(article)
    assert dto.publication_type == ""


# --------------------------------------------------------------------------------------------- #
# service.py -- the registry seam and both public entry points
# --------------------------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_serialize_article_to_metadata_xml_renders_via_the_registered_composer(accepted_article):
    xml = service.serialize_article_to_metadata_xml(accepted_article)

    assert xml.strip().startswith("<?xml")
    assert f'ms_no="{accepted_article.articleworkflow.preprint_id}"' in xml


@pytest.mark.django_db
def test_serialize_article_to_metadata_xml_falls_back_to_the_default_entry_for_an_unregistered_journal_code(
    accepted_article,
):
    with mock.patch.dict(service.PUBLISHER_COMPOSERS, {}, clear=True):
        service.PUBLISHER_COMPOSERS[None] = (mappers.build_article_export_dto, service.TEMPLATE_NAME)

        xml = service.serialize_article_to_metadata_xml(accepted_article)

    assert xml.strip().startswith("<?xml"), "an unregistered journal code must use the None default entry"


@pytest.mark.django_db
def test_build_production_export_zip_contains_metadata_xml_and_linked_files(accepted_article, freezer):
    """The zip must contain the same XML serialize_article_to_metadata_xml produces, plus every linked file."""
    # The article fixture pre-populates manuscript_files with placeholder File rows that have no
    # real content on disk (see wjs.jcom_profile.tests.conftest); drop them so only the two real,
    # on-disk files created below are linked.
    accepted_article.manuscript_files.clear()
    manuscript_file = save_file_to_article(
        DjangoFile(io.BytesIO(b"manuscript content"), name="article.pdf"),
        accepted_article,
        accepted_article.correspondence_author,
    )
    accepted_article.manuscript_files.add(manuscript_file)
    source_file = save_file_to_article(
        DjangoFile(io.BytesIO(b"source content"), name="manuscript.tex"),
        accepted_article,
        accepted_article.correspondence_author,
    )
    accepted_article.source_files.add(source_file)
    # `freezer` (pytest-freezer) pins `timezone.now()` so the two independent calls below --
    # one for the expected XML, one inside build_production_export_zip -- render identical
    # `export_date` values and can be compared for exact equality.
    expected_xml = service.serialize_article_to_metadata_xml(accepted_article)
    ms_no = accepted_article.articleworkflow.preprint_id

    zip_bytes = service.build_production_export_zip(accepted_article)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert set(archive.namelist()) == {
            f"{ms_no}/doc/",
            f"{ms_no}/doc/manuscript.tex",
            f"{ms_no}/pdf/",
            f"{ms_no}/pdf/{ms_no}.pdf",
            service.metadata_xml_entry_name(ms_no),
        }
        assert archive.read(service.metadata_xml_entry_name(ms_no)).decode() == expected_xml
        assert archive.read(f"{ms_no}/pdf/{ms_no}.pdf") == b"manuscript content"
        assert archive.read(f"{ms_no}/doc/manuscript.tex") == b"source content"


@pytest.mark.django_db
def test_build_production_export_zip_includes_empty_doc_folder_when_no_source_files(accepted_article):
    """doc/ is written even when there are no source files, matching IOP's own reference package."""
    accepted_article.manuscript_files.clear()
    manuscript_file = save_file_to_article(
        DjangoFile(io.BytesIO(b"manuscript content"), name="article.pdf"),
        accepted_article,
        accepted_article.correspondence_author,
    )
    accepted_article.manuscript_files.add(manuscript_file)
    accepted_article.source_files.clear()
    ms_no = accepted_article.articleworkflow.preprint_id

    zip_bytes = service.build_production_export_zip(accepted_article)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert f"{ms_no}/doc/" in archive.namelist()
        assert not any(name.startswith(f"{ms_no}/doc/") and name != f"{ms_no}/doc/" for name in archive.namelist())


@pytest.mark.django_db
def test_build_production_export_zip_directory_entries_are_enterable(accepted_article):
    """doc/ and pdf/ are extracted as directories with the execute bit (a bare ZipInfo would give 0600)."""
    accepted_article.manuscript_files.clear()
    manuscript_file = save_file_to_article(
        DjangoFile(io.BytesIO(b"manuscript content"), name="article.pdf"),
        accepted_article,
        accepted_article.correspondence_author,
    )
    accepted_article.manuscript_files.add(manuscript_file)
    accepted_article.source_files.clear()
    ms_no = accepted_article.articleworkflow.preprint_id

    zip_bytes = service.build_production_export_zip(accepted_article)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for name in (f"{ms_no}/doc/", f"{ms_no}/pdf/"):
            info = archive.getinfo(name)
            mode = info.external_attr >> 16
            assert info.is_dir(), f"{name} must be a directory entry"
            assert stat.S_ISDIR(mode), f"{name} must carry the Unix directory file type"
            assert stat.S_IMODE(mode) == 0o755, f"{name} must be extracted as rwxr-xr-x, got {oct(stat.S_IMODE(mode))}"


@pytest.mark.django_db
def test_build_production_export_zip_second_manuscript_file_keeps_original_filename(accepted_article):
    """A second manuscript_files entry (not the documented "one PDF" case) keeps its own filename."""
    accepted_article.manuscript_files.clear()
    first_manuscript = save_file_to_article(
        DjangoFile(io.BytesIO(b"first content"), name="article.pdf"),
        accepted_article,
        accepted_article.correspondence_author,
    )
    second_manuscript = save_file_to_article(
        DjangoFile(io.BytesIO(b"second content"), name="appendix.pdf"),
        accepted_article,
        accepted_article.correspondence_author,
    )
    accepted_article.manuscript_files.add(first_manuscript, second_manuscript)
    ms_no = accepted_article.articleworkflow.preprint_id

    zip_bytes = service.build_production_export_zip(accepted_article)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.read(f"{ms_no}/pdf/{ms_no}.pdf") == b"first content"
        assert archive.read(f"{ms_no}/pdf/appendix.pdf") == b"second content"


# --------------------------------------------------------------------------------------------- #
# Template contract -- hand-built DTO, no DB, no factories
# --------------------------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_template_renders_from_hand_built_dto():
    """
    Prove the template contract independently of any Django model or DB.

    Builds an ``ArticleExportDTO`` entirely from literal values and renders it through the real
    ``jcap_metadata_export.xml`` template, then asserts on specific rendered nodes.
    """
    dto = ArticleExportDTO(
        lang="en",
        ms_no="JCOM_2101_2022_A01",
        rev="2",
        rev_id=2,
        journal=JournalExportDTO(
            publisher_name="SISSA Medialab",
            full_journal_title="Journal of Communication",
            journal_abbreviation="JCOM",
            issn_print="",
            issn_digital="1824-2049",
        ),
        decision_status="accept",
        decision_label="Accepted",
        title="A Hand-Built Sample Title",
        subtitle="A Subtitle",
        publication_type="Article",
        authors=[
            AuthorExportDTO(
                seq=1,
                is_corresponding=True,
                user_id="42",
                salutation="Dr",
                first_name="Jane",
                middle_name="Q",
                last_name="Doe",
                email="jane.doe@example.org",
                orcid="0000-0002-1234-5678",
                affiliation=AffiliationExportDTO(
                    institution="Example University",
                    department="Physics",
                    person_title="Professor",
                    city="Trieste",
                    country="Italy",
                ),
            ),
        ],
        pii="JCOM",
        files=[
            FileExportDTO(
                designation="Complete Document for Review (PDF Only)",
                file_name="art.pdf",
                file_format="pdf",
            ),
        ],
        received_date=DateParts(year="2026", month="1", day="5"),
        revised_date=DateParts(),
        submitted_date=DateParts(year="2026", month="1", day="5"),
        decision_date=DateParts(year="2026", month="2", day="9"),
        abstract="A single-line abstract.",
        custom_fields=[
            CustomFieldExportDTO(code="OA Agreed", name="OA Requested?", value="No"),
        ],
        keywords=[KeywordExportDTO(id="1", name="testing")],
        total_pages="7",
        funders=[FunderExportDTO()],
        no_funders=True,
    )

    rendered = render_to_string(
        "wjs_review/metadata_export/jcap_metadata_export.xml",
        {"article": dto, "export_date": "2026-2-9 00:00:00.0"},
    )

    root = ET.fromstring(rendered)
    assert root.tag == "article_set", "IOP feedback: the whole document is wrapped in <article_set>"
    assert root.attrib["dtd_version"] == "4.28.5"

    article_el = root.find("article")
    assert article_el is not None, "the <article> element must be article_set's child, not the root"
    assert article_el.attrib["lang"] == "en"
    assert article_el.attrib["ms_no"] == "JCOM_2101_2022_A01"
    assert article_el.attrib["rev"] == "2"
    assert article_el.find("article_title").text == "A Hand-Built Sample Title"
    assert article_el.find("history/ms_id/rev_id").text == "2", "rev_id must be decoupled from the root @rev"

    author_el = article_el.find("author_list/author")
    assert author_el.attrib["corr"] == "true"
    assert author_el.find("last_name").text == "Doe"
    assert author_el.find("affiliation/inst").text == "Example University"
    assert author_el.find("affiliation/country").text == "Italy"

    assert article_el.find("history/ms_id/received_date/year").text == "2026"
    assert article_el.find("history/ms_id/revised_date/year") is None or article_el.find(
        "history/ms_id/revised_date/year",
    ).text in (None, "")

    assert article_el.find("fundref_information/no_funders").text == "True"


@pytest.mark.django_db
def test_template_escapes_abstract_special_characters():
    """
    Settled (export_spec.md): abstract must be XML-escaped, on top of being single-lined.

    Handled "for free" by Django's default template autoescaping (`{{ article.abstract }}` is
    not marked `|safe` anywhere in the template) -- this test proves that mechanism actually
    covers the requirement, rather than asserting on a formatter that doesn't do the escaping.
    """
    dto = ArticleExportDTO(abstract="A <p>paragraph</p> with a & an ampersand.")

    rendered = render_to_string(
        "wjs_review/metadata_export/jcap_metadata_export.xml",
        {"article": dto, "export_date": "2026-2-9 00:00:00.0"},
    )

    assert "&lt;p&gt;" in rendered, "literal HTML tags in the abstract must be escaped, not stripped or left raw"
    assert "&amp;" in rendered

    # And the escaped XML must still parse cleanly, round-tripping back to the original text:
    root = ET.fromstring(rendered)
    article_el = root.find("article")
    assert article_el.find("abstract").text == "A <p>paragraph</p> with a & an ampersand."


@pytest.mark.django_db
def test_template_wraps_article_in_article_set_with_doctype():
    """IOP feedback: the document envelope (DOCTYPE + <article_set> wrapper) was missing entirely."""
    dto = ArticleExportDTO()

    rendered = render_to_string(
        "wjs_review/metadata_export/jcap_metadata_export.xml",
        {"article": dto, "export_date": "2026-2-9 00:00:00.0"},
    )

    assert '<!DOCTYPE article_set SYSTEM "s1.dtd">' in rendered
    assert rendered.strip().endswith("</article_set>")

    root = ET.fromstring(rendered)
    assert root.tag == "article_set"
    assert len(root) == 1, "article_set must wrap exactly one <article> element"
    assert root[0].tag == "article"


def test_send_zip_to_iop_is_a_noop_stub():
    """No transport is implemented yet: the stub must not raise and must return nothing."""
    assert publishers.send_zip_to_iop(article=None, zip_bytes=b"PK\x03\x04") is None


def test_wjs_review_acceptance_zip_send_functions_jcap_entry_resolves():
    """The shipped WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS["JCAP"] path must resolve to send_zip_to_iop."""
    assert import_string(settings.WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS["JCAP"]) is publishers.send_zip_to_iop
