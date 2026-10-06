# Match the production export zip's structure to IOP's reference package

## Context

`metadata_export.service.build_production_export_zip(article)` builds the zip
`SendProductionXMLToPublisher` hands to `publishers.send_zip_to_iop` (via
`sftp._send_via_sftp`, `2026-09-23-iop-sftp-send-design.md`). Today it writes a flat
archive: `metadata.xml` at the root, plus every `manuscript_files`/`source_files` entry
under its own original filename, also at the root.

A real reference package IOP produced from their own tooling
(`JCAP_101P_0825.zip`, attached to `wjs/specs#2912`'s 2026-09-22 comment) has a
different, non-flat shape:

```
JCAP_101P_0825/
JCAP_101P_0825/doc/
JCAP_101P_0825/JCAP_101P_0825-metadata.xml
JCAP_101P_0825/pdf/
JCAP_101P_0825/pdf/JCAP_101P_0825.pdf
```

`doc/` is present as an explicit (empty) directory entry even though this particular
article has no source files — the only data point we have on that question, and the
one this design follows (see *Open questions*).

**Scope**: this change is contained entirely within
`build_production_export_zip` (plus two small new private helpers in the same
module). The XML's own content/DTD version is out of scope — already handled
elsewhere (`2026-09-02-iop-xml-export-design.md`); this only reshapes the zip package
around it.

## Design

### Folder/file mapping

`ms_no = mappers.map_ms_no(article)` (the same mapper `sftp.py` already uses for the
SFTP remote filename) becomes the zip's top-level folder name and the basis for every
entry inside it:

| Content | Zip path |
|---|---|
| (top-level folder) | `{ms_no}/` |
| Source files (`get_export_files`'s second group — Word/TeX/Figures/etc) | `{ms_no}/doc/{original_filename}` |
| Metadata XML | `{ms_no}/{ms_no}-metadata.xml` |
| Manuscript file (`get_export_files`'s first group — "Complete Document for Review, PDF Only") | `{ms_no}/pdf/{ms_no}.pdf` |

`doc/` and `pdf/` are written as **explicit zip directory entries** (`ZipInfo` with a
trailing `/`, zero-length), not left implicit — so an empty group (e.g. no source
files) still produces a `doc/` entry in the output, matching the reference package
even when nothing is inside it.

**Manuscript renaming**: `get_export_files`'s own docstring documents
`manuscript_files` as "Complete Document for Review (PDF Only)" — expected to hold
exactly one file per article in practice, even though it's a queryset. Only the
*first* manuscript file is renamed to `{ms_no}.pdf`; if the queryset unexpectedly
holds more than one (not supposed to happen, but not schema-enforced either), any
extras keep their original filename rather than silently colliding with or
overwriting the canonical name. Source files are never renamed — original filenames,
same as today, just relocated under `doc/`.

### Implementation sketch

```python
def _zip_directory_entry(archive: ZipFile, path: str) -> None:
    """Write path (must end in "/") as an explicit, empty zip directory entry."""
    archive.writestr(ZipInfo(path), b"")


def build_production_export_zip(article) -> bytes:
    ms_no = mappers.map_ms_no(article)
    xml = serialize_article_to_metadata_xml(article)
    manuscript_files, source_files = get_export_files(article)

    in_memory = BytesIO()
    with ZipFile(in_memory, "w") as archive:
        _zip_directory_entry(archive, f"{ms_no}/doc/")
        _zip_directory_entry(archive, f"{ms_no}/pdf/")
        archive.writestr(f"{ms_no}/{ms_no}-metadata.xml", xml)
        for i, file in enumerate(manuscript_files):
            name = f"{ms_no}.pdf" if i == 0 else file.original_filename
            archive.write(file.self_article_path(), arcname=f"{ms_no}/pdf/{name}")
        for file in source_files:
            archive.write(file.self_article_path(), arcname=f"{ms_no}/doc/{file.original_filename}")

    return in_memory.getvalue()
```

### `ZIP_XML_ENTRY_NAME` becomes a function

The module-level constant `ZIP_XML_ENTRY_NAME = "metadata.xml"` is read by
`test_production.py` to locate the XML entry in the built zip. Since the entry name
now depends on `ms_no`, the constant is replaced with a small public helper:

```python
def metadata_xml_entry_name(ms_no: str) -> str:
    return f"{ms_no}/{ms_no}-metadata.xml"
```

`build_production_export_zip` and any test both compute the expected path through
this one function, so they can't drift apart the way a duplicated f-string in each
place could.

## Testing

- `test_build_production_export_zip_contains_metadata_xml_and_linked_files`
  (`test_metadata_export.py`): rewritten for the new nested/renamed layout —
  `archive.namelist()` now asserts the full `{ms_no}/...` set including the two
  explicit directory entries, and the manuscript/source file entries move under
  their new paths with the manuscript renamed.
- New case: an article with manuscript files but *no* source files still produces
  an (empty) `{ms_no}/doc/` entry in the archive.
- New case: a second manuscript file (constructed directly against
  `article.manuscript_files`, bypassing the "one file" convention) keeps its
  original filename under `pdf/`, doesn't collide with `{ms_no}.pdf`.
- `test_send_production_xml_to_publisher_sends_zip_and_logs_message`
  (`test_production.py`): swaps its `service.ZIP_XML_ENTRY_NAME` read for
  `service.metadata_xml_entry_name(accepted_article.articleworkflow.preprint_id)`.

## Open questions

- Whether IOP's ingestion tooling *requires* the explicit empty `doc/` directory
  entry, or merely tolerates it because that's how their own zip tool happens to
  write packages, is unconfirmed — the one reference sample we have includes it, and
  this design matches that sample. If IOP's own tooling turns out not to care either
  way, this is free to simplify later.
