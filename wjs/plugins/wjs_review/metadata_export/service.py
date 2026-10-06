"""Public entry point for the JCAP/EM-style metadata XML export."""

from io import BytesIO
from zipfile import ZipFile, ZipInfo

from django.template.loader import render_to_string
from django.utils import timezone

from .mappers import build_article_export_dto, get_export_files, map_ms_no

#: Template used to render the export; see export_spec.md's "Template context surface" section
#: for the contract this template expects.
TEMPLATE_NAME = "wjs_review/metadata_export/jcap_metadata_export.xml"

#: Per-journal ``(compose_function, template_name)`` registry -- the seam a second publisher
#: would extend, matching this repo's ``WJS_ARTICLE_ASSIGNMENT_FUNCTIONS``/
#: ``WJS_REVIEW_CHECK_FUNCTIONS`` pattern (see the design doc's Architecture section). Scaffolded
#: now with a single JCAP entry -- no second publisher exists yet; adding one means registering a
#: new ``(compose_fn, template_name)`` pair here, not branching this module. ``None`` is the
#: default/fallback journal-code key.
PUBLISHER_COMPOSERS = {
    None: (build_article_export_dto, TEMPLATE_NAME),
    "JCAP": (build_article_export_dto, TEMPLATE_NAME),
}


def _build_export_date() -> str:
    """
    Return the export's generation timestamp as "YYYY-M-D HH:MM:SS.f" (no leading zeros on the date part).

    Not sourced from the article (per export_spec.md); matches the shape used by both reference
    fixtures -- the spec does not pin an exact format down, so this is a reasonable, documented
    choice rather than a settled requirement.
    """
    now = timezone.now()
    time_part = f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}.{now.microsecond // 100000}"
    return f"{now.year}-{now.month}-{now.day} {time_part}"


def serialize_article_to_metadata_xml(article) -> str:
    """
    Render ``article`` (a ``submission.models.Article``) as metadata export XML.

    Looks up ``(compose_function, template_name)`` for ``article.journal.code`` in
    ``PUBLISHER_COMPOSERS``, falling back to the ``None`` default entry -- the single public entry
    point, generic from the start; there is no JCAP-specific wrapper to keep in sync with it.

    Raises ``mappers.UnsupportedArticleStageForExportError`` (uncaught, by design) if ``article``
    isn't an accepted article -- this export only ever fires for accepted articles
    (export_spec.md).
    """
    compose, template_name = PUBLISHER_COMPOSERS.get(article.journal.code, PUBLISHER_COMPOSERS[None])
    dto = compose(article)
    return render_to_string(template_name, {"article": dto, "export_date": _build_export_date()})


def metadata_xml_entry_name(ms_no: str) -> str:
    """Return the zip path the rendered XML is stored under, inside the ``{ms_no}/`` folder."""
    return f"{ms_no}/{ms_no}-metadata.xml"


def _write_directory_entry(archive: ZipFile, path: str) -> None:
    """Write ``path`` (must end in "/") as an explicit, empty zip directory entry.

    A bare ``ZipInfo`` has no Unix mode, so ``unzip`` would extract the directory as ``drw-------`` (no execute
    bit: it could not be entered). The mode and the MS-DOS directory flag are what ``ZipFile`` itself sets for
    directories.
    """
    entry = ZipInfo(path)
    entry.external_attr = (0o40755 << 16) | 0x10
    archive.writestr(entry, b"")


def build_production_export_zip(article) -> bytes:
    """
    Build the production export zip, matching the shape of IOP's own reference package.

    ``{ms_no}/`` (``mappers.map_ms_no``) is the top-level folder. ``{ms_no}/doc/`` and
    ``{ms_no}/pdf/`` are always written as explicit zip directory entries -- even when a group
    below has no files -- matching the one reference package this shape is based on
    (2026-09-23-production-export-zip-structure-design.md). Contains
    ``metadata_xml_entry_name(ms_no)`` (the same XML ``serialize_article_to_metadata_xml``
    produces) plus every file returned by ``mappers.get_export_files`` -- the exact same
    manuscript/source files the XML's own ``<file_list>`` links to, so the two can't drift apart.

    The first manuscript file (the documented "Complete Document for Review, PDF Only" case) is
    renamed to ``{ms_no}.pdf`` under ``pdf/``; any further manuscript_files entries (not supposed
    to happen, but not schema-enforced either) keep their own filename instead of colliding with
    that name. Source files keep their original filename, under ``doc/``.
    """
    ms_no = map_ms_no(article)
    xml = serialize_article_to_metadata_xml(article)
    manuscript_files, source_files = get_export_files(article)

    in_memory = BytesIO()
    with ZipFile(in_memory, "w") as archive:
        _write_directory_entry(archive, f"{ms_no}/doc/")
        _write_directory_entry(archive, f"{ms_no}/pdf/")
        archive.writestr(metadata_xml_entry_name(ms_no), xml)
        for index, file in enumerate(manuscript_files):
            name = f"{ms_no}.pdf" if index == 0 else file.original_filename
            archive.write(file.self_article_path(), arcname=f"{ms_no}/pdf/{name}")
        for file in source_files:
            archive.write(file.self_article_path(), arcname=f"{ms_no}/doc/{file.original_filename}")

    return in_memory.getvalue()
