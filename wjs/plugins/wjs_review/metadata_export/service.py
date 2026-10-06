"""Public entry point for the JCAP/EM-style metadata XML export."""

from io import BytesIO
from zipfile import ZipFile

from django.template.loader import render_to_string
from django.utils import timezone

from .mappers import build_article_export_dto, get_export_files

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


#: Name the rendered XML is stored under inside the production export zip.
ZIP_XML_ENTRY_NAME = "metadata.xml"


def build_production_export_zip(article) -> bytes:
    """
    Build the production export zip: the article's metadata XML plus every file it links to.

    Contains ``ZIP_XML_ENTRY_NAME`` (the same XML ``serialize_article_to_metadata_xml`` produces)
    and one entry per file returned by ``mappers.get_export_files`` -- the exact same
    manuscript/source files the XML's own ``<file_list>`` links to, so the two can't drift apart.
    Entries are written from each ``core.File``'s ``self_article_path()``, under its
    ``original_filename``.
    """
    xml = serialize_article_to_metadata_xml(article)
    manuscript_files, source_files = get_export_files(article)

    in_memory = BytesIO()
    with ZipFile(in_memory, "w") as archive:
        archive.writestr(ZIP_XML_ENTRY_NAME, xml)
        for file in (*manuscript_files, *source_files):
            archive.write(file.self_article_path(), arcname=file.original_filename)

    return in_memory.getvalue()
