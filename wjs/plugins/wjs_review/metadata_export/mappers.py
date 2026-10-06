"""
Django model -> ``dto.py`` mapper functions for the JCAP/EM-style metadata export.

Every function here takes real Django model instances (``Article``, ``FrozenAuthor``,
``ControlledAffiliation``, ``Galley``, ``ArticleFunding``, ...) and returns plain ``dto.py``
instances, doing all the ORM lookups and business-logic decisions described in
``export_spec.md``. ``build_article_export_dto`` is the top-level composition function and is
kept a thin composition of the smaller per-concern mappers below -- it does no lookups itself; it
delegates to the generic ``compose_dto`` helper driven by the declarative ``JCAP_FIELDS`` spec.

Error policy (settled, export_spec.md): these mappers **degrade gracefully**, never raise, on
missing/unexpected *data* (e.g. no manuscript file, no ``ArticleWorkflow`` row). The one
deliberate exception is ``map_decision_status``/``map_decision_label``, which raise
``UnsupportedArticleStageForExportError`` for a non-accepted article -- a precondition violation
(this export only ever fires for accepted articles), not a data-completeness gap.
"""

import os
import subprocess
from typing import Any, Callable, TypeVar

from django.conf import settings
from submission.models import STAGE_ACCEPTED

from ..models import ArticleWorkflow
from .dto import (
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
from .formatters import (
    build_date_parts,
    format_date_dd_mon_yyyy,
    to_single_line,
    to_two_letter_language_code,
)

#: article/@rev -- settled as a fixed literal (the XML schema/format version) sourced from a
#: per-journal setting, not derived from the article at all. Settled default: ``"2"``. No
#: per-journal override is defined yet (export_spec.md) -- every journal currently gets the same
#: default; override via the ``REVIEW_METADATA_EXPORT_REV_BY_JOURNAL_CODE`` Django setting,
#: shaped like ``DEFAULT_OA_MESSAGE_CODES`` in ``wjs_submission/settings.py``
#: (``{journal_code: value, ...}``, ``None`` as the default/fallback key).
DEFAULT_EXPORT_REV_BY_JOURNAL_CODE = {
    None: "2",
}
EXPORT_REV_BY_JOURNAL_CODE = getattr(
    settings,
    "REVIEW_METADATA_EXPORT_REV_BY_JOURNAL_CODE",
    DEFAULT_EXPORT_REV_BY_JOURNAL_CODE,
)

#: Per-journal allowlist of ``AccessMode.code`` values counted as "OA agreed" for the
#: "OA Requested?" custom field. Settled shape (export_spec.md): modeled on the existing
#: ``wjs_submission.settings.DEFAULT_OA_MESSAGE_CODES``/``OA_MESSAGE_CODES`` pattern --
#: ``{journal_code: [access_mode_code, ...], None: [access_mode_code, ...]}``, ``None`` as the
#: default/fallback key. Settled default: ``["open-access"]``. No per-journal override is defined
#: yet -- override via the ``REVIEW_METADATA_OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE`` Django
#: setting. Settled default (MR !1487 review): every OA-flavored ``AccessMode`` code counts,
#: i.e. all of them except ``subscription``.
DEFAULT_OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE = {
    None: [
        "open-access",
        "open-access-paid",
        "oa-transformative-agreement",
        "oa-cern",
        "oa-cern-affiliated",
    ],
}
OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE = getattr(
    settings,
    "REVIEW_METADATA_OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE",
    DEFAULT_OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE,
)


class UnsupportedArticleStageForExportError(Exception):
    """
    Raised when exporting an article whose stage this export doesn't support.

    Settled (export_spec.md): this export only ever fires for accepted articles -- there is no
    other supported entry point. This is a deliberate carve-out from this module's general
    "degrade gracefully, never raise" policy: it is a precondition violation (calling the export
    against an article in the wrong stage), not a data-completeness gap on an otherwise-valid
    accepted article, and so it should fail loudly rather than emit empty/best-effort XML.
    """


def _get_workflow(article) -> ArticleWorkflow | None:
    """Return ``article.articleworkflow``, or ``None`` if the article has no workflow row."""
    return getattr(article, "articleworkflow", None)


def map_language(article) -> str:
    """Map ``article.language`` (alpha-3) to the export's alpha-2 ``@lang`` code."""
    return to_two_letter_language_code(article.language)


def map_ms_no(article) -> str:
    """
    Resolve the export ``ms_no`` from ``ArticleWorkflow.preprint_id``.

    Settled (export_spec.md): always this source now -- the earlier
    ``Identifier(id_type="preprintid")`` wjApp-import fallback is dropped entirely, since
    wjApp-imported articles are out of scope for this export.
    """
    workflow = _get_workflow(article)
    return workflow.preprint_id if workflow is not None else ""


def map_rev(article) -> str:
    """Return the export's per-journal fixed schema/format-version literal for ``article/@rev``."""
    return EXPORT_REV_BY_JOURNAL_CODE.get(article.journal.code, EXPORT_REV_BY_JOURNAL_CODE[None])


def map_journal(article) -> JournalExportDTO:
    """Map ``article.journal`` to the export ``<journal>`` block."""
    journal = article.journal
    return JournalExportDTO(
        publisher_name=journal.publisher or "",
        full_journal_title=journal.name or "",
        journal_abbreviation=journal.code or "",
        issn_print=journal.print_issn or "",
        issn_digital=journal.issn or "",
    )


def map_decision_status(article) -> str:
    """
    Map ``article.stage`` to the export ``decision_status`` ("accept" only).

    Settled (export_spec.md): only the "accept" case is supported. Any other stage raises
    ``UnsupportedArticleStageForExportError`` rather than emitting empty XML -- this export only ever
    fires for accepted articles, so reject/revise/pending and other stages are unreachable by
    design, not just unmapped.
    """
    if article.stage == STAGE_ACCEPTED:
        return "accept"
    raise UnsupportedArticleStageForExportError(
        f"metadata export only supports accepted articles; got stage={article.stage!r}",
    )


def map_decision_label(article) -> str:
    """Map ``article.stage`` to the export ``decision_label`` ("Accepted" only). See ``map_decision_status``."""
    if article.stage == STAGE_ACCEPTED:
        return "Accepted"
    raise UnsupportedArticleStageForExportError(
        f"metadata export only supports accepted articles; got stage={article.stage!r}",
    )


def _affiliation_dto_from_controlled_affiliation(controlled_affiliation) -> AffiliationExportDTO:
    """Build an ``AffiliationExportDTO`` from a single ``ControlledAffiliation`` row (empty if ``None``)."""
    if controlled_affiliation is None:
        return AffiliationExportDTO()

    organization = controlled_affiliation.organization
    institution = str(organization) if organization else ""
    city = ""
    country = ""
    if organization is not None:
        location = organization.location
        if location is not None:
            city = location.name or ""
            country = str(location.country) if location.country else ""

    return AffiliationExportDTO(
        institution=institution,
        department=controlled_affiliation.department or "",
        person_title=controlled_affiliation.title or "",
        city=city,
        country=country,
    )


def _is_ta_agreed(article) -> bool:
    """Return whether the article's access mode is specifically the transformative-agreement one."""
    submission_data = getattr(article, "submission_data", None)
    if submission_data is None or submission_data.access_mode is None:
        return False
    return submission_data.access_mode.code == "oa-transformative-agreement"


def _is_corresponding_author(frozen_author, article) -> bool:
    """Return whether this ``FrozenAuthor`` is the article's correspondence author."""
    return bool(
        frozen_author.author_id
        and article.correspondence_author_id
        and frozen_author.author_id == article.correspondence_author_id,
    )


def map_affiliation(frozen_author, article) -> AffiliationExportDTO:
    """
    Map a ``FrozenAuthor``'s primary (or first) ``ControlledAffiliation`` to the export shape.

    Multiple affiliations are out of scope for this first pass -- only the primary/first one is
    exported, per export_spec.md. Confirmed as intentional (not a gap): this is each individual
    author's own affiliation, not a single article-level/paper affiliation.

    Re-settled (IOP feedback): for the **corresponding author** of a **transformative-agreement**
    article (``article.submission_data.access_mode.code == "oa-transformative-agreement"``), when
    ``article.submission_data.affiliation`` is set, that ``ControlledAffiliation`` -- the one
    actually eligible for the TA -- is used instead of the per-author lookup below. It need not be
    whichever affiliation happens to be marked primary on the corresponding author's own record.
    Every other author, and every non-TA article, uses the per-author lookup unchanged.
    """
    is_corresponding = _is_corresponding_author(frozen_author, article)
    if is_corresponding and _is_ta_agreed(article):
        ta_affiliation = article.submission_data.affiliation
        if ta_affiliation is not None:
            return _affiliation_dto_from_controlled_affiliation(ta_affiliation)

    controlled_affiliation = (
        frozen_author.controlledaffiliation_set.filter(is_primary=True).first()
        or frozen_author.controlledaffiliation_set.first()
    )
    return _affiliation_dto_from_controlled_affiliation(controlled_affiliation)


def map_author(frozen_author, article, seq=None) -> AuthorExportDTO:
    """
    Map one ``FrozenAuthor`` (in the context of its ``article``) to the export author shape.

    ``seq`` should always be passed by real callers (see ``map_authors``): the ``frozen_author.order``
    fallback used when it's omitted is not guaranteed consecutive and is not IOP-safe on its own.
    """
    is_corresponding = _is_corresponding_author(frozen_author, article)
    # Settled (export_spec.md): always FrozenAuthor.pk, never frozen_author.author_id/linked
    # Account id -- an Account's real id means nothing to IOP and risks colliding with a
    # different ID space than FrozenAuthor.pk's own.
    user_id = str(frozen_author.pk)

    # FrozenAuthor.email already falls back frozen_email -> linked Account.email; guarded against
    # None (no frozen_email and no linked account), which the property itself doesn't do -- an
    # unguarded None would render as the literal string "None" in the XML.
    email = frozen_author.email or ""
    salutation = frozen_author.name_prefix or "Dr."

    return AuthorExportDTO(
        seq=frozen_author.order if seq is None else seq,
        is_corresponding=is_corresponding,
        user_id=user_id,
        salutation=salutation,
        first_name=frozen_author.first_name,
        middle_name=frozen_author.middle_name,
        last_name=frozen_author.last_name,
        email=email or "",
        orcid=frozen_author.frozen_orcid,
        affiliation=map_affiliation(frozen_author, article),
    )


def map_authors(article) -> list[AuthorExportDTO]:
    """
    Map every ``article.frozenauthor_set``, ordered, to the export ``<author_list>``.

    Settled (IOP feedback): ``@author_seq`` is the author's 1-indexed **position** in this
    ordering, not the raw ``FrozenAuthor.order`` value -- ``order`` has no DB constraint keeping
    it gap-free (author reordering/removal can leave e.g. ``1, 5, 9``), and IOP requires
    ``@author_seq`` to be consecutive. ``order`` still decides the *ordering*.
    """
    return [
        map_author(frozen_author, article, seq=position)
        for position, frozen_author in enumerate(article.frozenauthor_set.order_by("order"), start=1)
    ]


def corresponding_author_affiliation_is_incomplete(article) -> bool:
    """
    Return whether the corresponding author's exported affiliation is missing inst/city/country.

    Settled (IOP feedback): IOP requires these three fields non-empty for the corresponding
    author specifically -- an empty value means their team has to populate it manually from the
    reference PDF. ``ControlledAffiliation.organization`` is nullable (and even when set,
    ``Organization.location`` can itself be unset), so this isn't guaranteed by the data model;
    this check exists so a gap surfaces as an EO message instead of an incomplete export.
    """
    if article.correspondence_author_id is None:
        return True
    frozen_author = article.frozenauthor_set.filter(author_id=article.correspondence_author_id).first()
    if frozen_author is None:
        return True
    affiliation = map_affiliation(frozen_author, article)
    return not (affiliation.institution and affiliation.city and affiliation.country)


def map_pii(article) -> str:
    """Map the export ``<article_id_list><article_id id_type="pii">`` (just the journal code)."""
    return article.journal.code or ""


def _extension_of(filename: str) -> str:
    """Return a filename's extension, lower-cased and without the leading dot ("" if none)."""
    _, ext = os.path.splitext(filename or "")
    return ext[1:].lower() if ext else ""


def get_export_files(article) -> tuple[list, list]:
    """
    Return the article's exported files as ``(manuscript_files, source_files)``.

    Two groups, in this order (settled, export_spec.md -- changed from the original proposal):
    1. one ``core.File`` per ``article.manuscript_files.all()`` entry -- "Complete Document for
       Review (PDF Only)". **Not** ``article.galley_set``: at "accepted" stage there generally
       are no post-typesetting galleys yet.
    2. one ``core.File`` per ``article.source_files.all()`` entry **only** -- "Source Files
       (incl. Word, TeX, Figures, etc)". ``article.supplementary_files`` (ESM) is dropped: those
       aren't source files per the schema's own ``file_designation`` semantics.

    Shared by ``map_files`` (the XML's ``<file_list>``) and the production export zip builder
    (``metadata_export.service.build_production_export_zip``), so both stay in lock-step about
    which files are "linked" in an export.
    """
    return list(article.manuscript_files.all()), list(article.source_files.all())


def map_files(article) -> list[FileExportDTO]:
    """
    Map the article's manuscript and source files to the export ``<file_list>``.

    ``file_format``/``file_extension`` are derived from the filename's extension in both groups,
    since ``core.File`` has no ``.type`` the way ``Galley`` does.
    """
    manuscript_files, source_files = get_export_files(article)
    files: list[FileExportDTO] = []

    for manuscript_file in manuscript_files:
        files.append(
            FileExportDTO(
                designation="Complete Document for Review (PDF Only)",
                file_name=manuscript_file.original_filename,
                file_format=_extension_of(manuscript_file.original_filename),
            ),
        )

    for source_file in source_files:
        files.append(
            FileExportDTO(
                designation="Source Files (incl. Word, TeX, Figures, etc)",
                file_name=source_file.original_filename,
                file_format=_extension_of(source_file.original_filename),
            ),
        )

    return files


def _latest_major_revision_editor_decision(article):
    """Return the article's newest (latest-created) major-revision ``EditorDecision``, or ``None``."""
    workflow = _get_workflow(article)
    if workflow is None:
        return None
    return workflow.decisions.filter(decision=ArticleWorkflow.Decisions.MAJOR_REVISION).order_by("-created").first()


def map_rev_id(article) -> int:
    """
    Map ``history/ms_id/rev_id``: how many major revisions the article has been through.

    Re-settled (MR !1487 review, supersedes the earlier ``review_round.round_number``-of-the-
    newest-major-revision approach): a straight count of ``EditorDecision`` rows with
    ``decision=MAJOR_REVISION``. The round-number approach over-counts whenever a minor or
    technical revision round falls between two major-revision rounds -- e.g. a minor revision in
    round 1 followed by a major revision in round 2 would report ``rev_id=2`` even though it is
    the article's *first* major revision.
    """
    workflow = _get_workflow(article)
    if workflow is None:
        return 0
    return workflow.decisions.filter(decision=ArticleWorkflow.Decisions.MAJOR_REVISION).count()


def _latest_major_revision_request(article):
    """Return the article's latest major-revision ``RevisionRequest`` (by ``date_requested``)."""
    return (
        article.revisionrequest_set.filter(type=ArticleWorkflow.Decisions.MAJOR_REVISION)
        .order_by("-date_requested")
        .first()
    )


def map_received_date(article) -> DateParts:
    """
    Map ``history/received_date``: the article's original, first-ever submission date.

    Terminology note (MR !1487 review): IOP's three `<history>` date fields don't mean what their
    names suggest at first glance, and are easy to cross-wire with each other:
    - ``received_date`` (this function) -- the article's *original* submission, before any review.
    - ``revised_date`` (``map_revised_date``) -- when the *revision was requested* by the editor,
      not when the author sent it back in.
    - ``submitted_date`` (``map_submitted_date``) -- when the author *submitted that revision*,
      not the article's original submission (that's ``received_date`` above).
    """
    return build_date_parts(article.date_submitted)


def map_revised_date(article) -> DateParts:
    """
    Map ``history/revised_date``: the decision date of the newest major-revision ``EditorDecision``.

    Settled (export_spec.md): scoped to the article's latest **major** revision, not just the
    latest ``RevisionRequest`` of any kind -- empty if the article has never had a major revision.
    """
    major_revision_decision = _latest_major_revision_editor_decision(article)
    if major_revision_decision is None:
        return DateParts()
    return build_date_parts(major_revision_decision.modified or major_revision_decision.created)


def map_submitted_date(article) -> DateParts:
    """
    Map ``history/submitted_date``: the date the author submitted that same major revision.

    Settled (export_spec.md): no fallback -- stays empty whenever the article has never had a
    major revision (straight accept, or only minor/technical revisions along the way).
    """
    major_revision_request = _latest_major_revision_request(article)
    if major_revision_request is None:
        return DateParts()
    return build_date_parts(major_revision_request.date_completed)


def map_decision_date(article) -> DateParts:
    """
    Map ``history/decision_date``.

    Re-settled (MR !1487 review): plain ``article.date_accepted``. This export only ever fires
    for accepted articles (see ``UnsupportedArticleStageForExportError``), so ``date_accepted`` is
    always set by then and ``date_declined`` is always ``None`` (``Article.accept_article()``
    clears it) -- the earlier ``EditorDecision``-based lookup (with its own
    ``date_accepted``/``date_declined`` fallback for articles with no matching row) was needless
    complexity for a value ``accept_article()`` already stamps on the article itself.
    """
    return build_date_parts(article.date_accepted)


def _is_oa_agreed(article) -> bool:
    """Return whether the article's access mode is in the (placeholder) OA-agreed allowlist."""
    submission_data = getattr(article, "submission_data", None)
    if submission_data is None or submission_data.access_mode is None:
        return False
    allowlist = OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE.get(
        article.journal.code,
        OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE[None],
    )
    return submission_data.access_mode.code in allowlist


def map_custom_fields(article) -> list[CustomFieldExportDTO]:
    """
    Map the ``<configurable_data_fields><custom_fields>`` entries, in the spec's fixed order.

    Re-settled (IOP feedback): when OA isn't agreed, "OA date requested" and "OA licence type"
    are **omitted from the list entirely**, not emitted with an empty value -- so this produces
    6 ``CustomFieldExportDTO`` rows when OA is agreed, 4 when it isn't. "Section" and
    "Additional Authors" are never emitted -- both duplicated information already present
    elsewhere in the export (see the comment near the end of this function).
    """
    fields: list[CustomFieldExportDTO] = []

    oa_agreed = _is_oa_agreed(article)
    fields.append(CustomFieldExportDTO(code="OA Agreed", name="OA Requested?", value="Yes" if oa_agreed else "No"))

    if oa_agreed:
        # "OA date requested" uses the article's acceptance date, not its submission date -- per
        # export_spec.md this is the acceptance date for OA-agreed articles, not a literal "when
        # was OA requested" timestamp (Janeway has no such field).
        oa_date_requested = format_date_dd_mon_yyyy(article.date_accepted)
        fields.append(
            CustomFieldExportDTO(code="OA date requested", name="Date OA Requested", value=oa_date_requested),
        )

        license_short_name = article.license.short_name if article.license else ""
        fields.append(CustomFieldExportDTO(code="OA licence type", name="OA Licence Type", value=license_short_name))

    # Re-settled (IOP feedback): IOP confirmed this field accepts only a small preset vocabulary
    # -- "Open Access" when OA Agreed, else the fixed literal "Standard" -- never an arbitrary
    # license.short_name. A third value exists (a non-standard copyright agreement) but IOP
    # notifies the journal about that case directly; this mapper never generates it.
    copyright_licence_value = "Open Access" if oa_agreed else "Standard"
    fields.append(
        CustomFieldExportDTO(
            code="Copyright/Licence Type",
            name="Copyright/Licence Type",
            value=copyright_licence_value,
        ),
    )

    # Explicitly deferred, per export_spec.md -- never populated from any field.
    fields.append(CustomFieldExportDTO(code="Production Comments", name="Production Comments", value=""))

    special_issue_name = "Special Issue Title"
    special_issue_value = ""
    primary_issue = article.primary_issue
    if (
        primary_issue is not None
        and primary_issue.issue_type is not None
        and primary_issue.issue_type.code == "collection"
    ):
        special_issue_name = primary_issue.issue_title
        special_issue_value = primary_issue.short_name
    fields.append(CustomFieldExportDTO(code="Special Issue", name=special_issue_name, value=special_issue_value))

    # Re-settled (IOP feedback): no "Section" custom field -- it duplicated `<publication_type>`,
    # which already carries `article.section.name` as the article type. No "Additional Authors"
    # custom field either -- every author is already listed individually in `<author_list>`, so
    # repeating their names here was redundant.

    return fields


def map_keywords(article) -> list[KeywordExportDTO]:
    """Map ``article.keywords`` to the export ``<content><attr_type>`` attributes."""
    return [KeywordExportDTO(id=str(keyword.pk), name=keyword.word) for keyword in article.keywords.all()]


def _count_manuscript_pages(article) -> int | None:
    """
    Best-effort PDF page count for the article's primary manuscript file, via ``pdfinfo``.

    Returns ``None`` (never raises) if there is no manuscript file, its path can't be resolved,
    the file isn't a PDF, or ``pdfinfo`` itself is unavailable/fails -- consistent with this
    module's "mappers degrade gracefully" policy (export_spec.md).
    """
    manuscript = article.manuscript_files.first()
    if manuscript is None:
        return None
    path = manuscript.self_article_path()
    if not path or not path.lower().endswith(".pdf"):
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed binary name, no shell, args not user-controlled
            ["pdfinfo", path],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def map_total_pages(article) -> str:
    """
    Map ``article.total_pages`` to its export string form ("" if unset).

    Settled (export_spec.md): if ``article.total_pages`` isn't already set, this computes it
    (via ``pdfinfo`` against the manuscript, see ``_count_manuscript_pages``) and **persists it
    onto ``Article.total_pages``** at mapping time -- a deliberate write side-effect, unusual for
    this module's otherwise read-only mappers, so that later exports don't recompute it.
    """
    if not article.total_pages:
        page_count = _count_manuscript_pages(article)
        if page_count:
            article.total_pages = page_count
            article.save(update_fields=["total_pages"])
    return str(article.total_pages) if article.total_pages else ""


def map_funders_list(article) -> list[FunderExportDTO]:
    """
    Map ``article.articlefunding_set`` to ``<fundref_information><funder>`` entries.

    When there are no ``ArticleFunding`` rows, still returns exactly one empty placeholder
    ``FunderExportDTO``, matching the shape both reference fixtures use when ``no_funders=True``.
    """
    funding_rows = list(article.articlefunding_set.all())
    if not funding_rows:
        return [FunderExportDTO()]
    return [
        FunderExportDTO(name=funding.name, fundref_id=funding.fundref_id or "", funding_id=funding.funding_id or "")
        for funding in funding_rows
    ]


def map_no_funders(article) -> bool:
    """Map ``fundref_information/no_funders``: ``True`` iff ``article.articlefunding_set`` is empty."""
    return not article.articlefunding_set.exists()


def map_title(article) -> str:
    """Map ``article.title`` to ``<article_title>``."""
    return article.title or ""


def map_subtitle(article) -> str:
    """
    Map ``article.subtitle`` to ``<article_sub_title>`` (deprecated field, expected empty).

    ``Article.subtitle`` is ``null=True`` -- coalesce to ``""`` so the export never renders the
    literal text "None" (export_spec.md).
    """
    return article.subtitle or ""


def map_publication_type(article) -> str:
    """
    Map ``article.section.name`` to ``<publication_type>``.

    ``Section.name`` is ``null=True`` even when ``article.section`` is set -- ``if article.section``
    alone does not guard against a ``None`` name (export_spec.md); the same value feeds the
    "Section" ``<custom_fields>`` entry in ``map_custom_fields``.
    """
    return (article.section.name or "") if article.section else ""


def map_abstract(article) -> str:
    """
    Map ``article.abstract`` to ``<abstract>``: single-line text.

    ``to_single_line`` handles whitespace normalization only; XML-escaping (settled,
    export_spec.md) is handled by the template's default Django autoescaping (``{{ }}``), not
    here -- see the template-contract test asserting ``<``/``&`` come out escaped.
    """
    return to_single_line(article.abstract or "")


DTOType = TypeVar("DTOType")


def compose_dto(article, dto_class: type[DTOType], field_spec: dict[str, Callable[[Any], Any]]) -> DTOType:
    """Build ``dto_class(**{name: mapper(article) for name, mapper in field_spec.items()})``."""
    return dto_class(**{name: mapper(article) for name, mapper in field_spec.items()})


#: The declarative field spec for JCAP: {ArticleExportDTO field name: article -> value mapper}.
#: Settled (export_spec.md): the seam a second publisher extends by defining its own {field_name:
#: mapper} dict (reusing entries from this one, overriding representation where it differs) and a
#: dto_class/compose function registered into service.PUBLISHER_COMPOSERS -- not by subclassing or
#: branching this module.
JCAP_FIELDS = {
    "lang": map_language,
    "ms_no": map_ms_no,
    "rev": map_rev,
    "rev_id": map_rev_id,
    "journal": map_journal,
    "decision_status": map_decision_status,
    "decision_label": map_decision_label,
    "title": map_title,
    "subtitle": map_subtitle,
    "publication_type": map_publication_type,
    "authors": map_authors,
    "pii": map_pii,
    "files": map_files,
    "received_date": map_received_date,
    "revised_date": map_revised_date,
    "submitted_date": map_submitted_date,
    "decision_date": map_decision_date,
    "abstract": map_abstract,
    "custom_fields": map_custom_fields,
    "keywords": map_keywords,
    "total_pages": map_total_pages,
    "funders": map_funders_list,
    "no_funders": map_no_funders,
}


def build_article_export_dto(article) -> ArticleExportDTO:
    """
    Compose an ``ArticleExportDTO`` for ``article`` from ``JCAP_FIELDS``. The single top-level entry point.

    Raises ``UnsupportedArticleStageForExportError`` if ``article`` isn't accepted -- see
    ``map_decision_status``/``map_decision_label``.
    """
    return compose_dto(article, ArticleExportDTO, JCAP_FIELDS)
