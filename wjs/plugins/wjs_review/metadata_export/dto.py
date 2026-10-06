"""
Plain data-transfer objects for the JCAP/EM-style metadata XML export.

Every field here is an already-resolved, plain value (``str``/``int``/``bool``/a nested DTO or
list of DTOs). There is deliberately **no** import of any Django model, and no method beyond a
trivial ``__str__``/``__bool__`` -- these objects must be constructible with literal values in a
test with zero DB/Django model involvement. All the Article/FrozenAuthor/... -> DTO resolution
logic lives in ``mappers.py``, not here.

This module also defines the exact "template context surface" contract documented at the end of
``export_spec.md`` -- ``ArticleExportDTO`` is the object handed to the
``wjs_review/metadata_export/jcap_metadata_export.xml`` template as ``article``.
"""

import dataclasses


@dataclasses.dataclass
class DateParts:
    """
    A calendar date broken into plain string parts, with no leading zeros.

    An "empty"/unset date is represented by the default all-empty-strings instance, which is
    falsy so callers (and the template) can test ``if date_parts:`` to decide whether to render
    the sub-elements at all.
    """

    year: str = ""
    month: str = ""
    day: str = ""

    def __bool__(self) -> bool:
        """Treat a DateParts as falsy when it has no year (i.e. it is unset)."""
        return bool(self.year)


@dataclasses.dataclass
class JournalExportDTO:
    """Journal-level fields, see export_spec.md's ``<journal>`` section."""

    publisher_name: str = ""
    full_journal_title: str = ""
    journal_abbreviation: str = ""
    issn_print: str = ""
    issn_digital: str = ""


@dataclasses.dataclass
class AffiliationExportDTO:
    """The (single, primary) affiliation exported for one author."""

    institution: str = ""
    department: str = ""
    person_title: str = ""
    city: str = ""
    country: str = ""


@dataclasses.dataclass
class AuthorExportDTO:
    """One entry of ``<author_list><author>``."""

    seq: int = 0
    is_corresponding: bool = False
    user_id: str = ""
    salutation: str = ""
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    email: str = ""
    orcid: str = ""
    affiliation: AffiliationExportDTO = dataclasses.field(default_factory=AffiliationExportDTO)

    def __str__(self) -> str:
        """Render as "First Middle Last", used for the "Additional Authors" custom field."""
        return " ".join(part for part in (self.first_name, self.middle_name, self.last_name) if part)


@dataclasses.dataclass
class FileExportDTO:
    """One entry of ``<file_list><file>``."""

    designation: str = ""
    file_name: str = ""
    file_format: str = ""


@dataclasses.dataclass
class CustomFieldExportDTO:
    """One entry of ``<configurable_data_fields><custom_fields>``."""

    code: str = ""
    name: str = ""
    value: str = ""


@dataclasses.dataclass
class KeywordExportDTO:
    """One entry of ``<content><attr_type><attribute>``."""

    id: str = ""  # noqa: A003 -- required name, matches the template contract ("keyword.id")
    name: str = ""


@dataclasses.dataclass
class FunderExportDTO:
    """One entry of ``<fundref_information><funder>``."""

    name: str = ""
    fundref_id: str = ""
    funding_id: str = ""


@dataclasses.dataclass
class ArticleExportDTO:
    """
    The root object handed to the export template as ``article``.

    Field names/shapes match the "Template context surface" contract in export_spec.md exactly.
    """

    lang: str = ""
    ms_no: str = ""
    #: article/@rev -- settled as a fixed literal (the XML schema/format version) from a
    #: per-journal setting, not derived from the article, decoupled from ``rev_id`` below.
    rev: str = ""
    #: history/ms_id/rev_id -- settled: the review round of the newest (latest-created)
    #: major-revision EditorDecision. No longer the same source as ``rev`` above.
    rev_id: int = 0
    journal: JournalExportDTO = dataclasses.field(default_factory=JournalExportDTO)
    decision_status: str = ""
    decision_label: str = ""
    title: str = ""
    subtitle: str = ""
    publication_type: str = ""
    authors: list[AuthorExportDTO] = dataclasses.field(default_factory=list)
    pii: str = ""
    files: list[FileExportDTO] = dataclasses.field(default_factory=list)
    received_date: DateParts = dataclasses.field(default_factory=DateParts)
    revised_date: DateParts = dataclasses.field(default_factory=DateParts)
    submitted_date: DateParts = dataclasses.field(default_factory=DateParts)
    decision_date: DateParts = dataclasses.field(default_factory=DateParts)
    abstract: str = ""
    custom_fields: list[CustomFieldExportDTO] = dataclasses.field(default_factory=list)
    keywords: list[KeywordExportDTO] = dataclasses.field(default_factory=list)
    total_pages: str = ""
    funders: list[FunderExportDTO] = dataclasses.field(default_factory=list)
    no_funders: bool = True
