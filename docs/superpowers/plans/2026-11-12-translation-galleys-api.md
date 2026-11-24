# Translation galleys API — implementation plan

Issue: wjs/specs#990. Follow-ups: wjs/specs#3398 (naming convention and upload check), wjs/specs#3399 (helper
`.tex` files), wjs/specs#3400 (refactor of the duplicated jcomassistant calls).

## Context

Some papers (e.g. JCOM_1516) are published together with a translation. The typesetter's archive then contains two
TeX files: the main one and the translation.

- jcomassistant (JA) v3 (wjs/jcomassistant-project@8ade0e7d, 2026-03-24) builds the galleys of **one** TeX file per
  request: the one named in `spawn.ini` (`[tex] main=...`), or the only journal `.tex` of the archive. No
  `spawn.ini` directive asks for a second source. The translation is never compiled.
- WJS (`AttachGalleys`) expects one galley per format, and labels galleys without the language.

Changing JA or the galley machinery of WJS is out of scope for now (wjs/wjs-profile-project!1625 is refactoring
`AttachGalleys` and `logic__production.py`). The chosen "manual" approach:

1. the paper is published as usual, with the galleys of the main file;
2. a new API entry point builds the galleys of the translation, with the **same** publication data (DOI, date,
   volume/issue/eid/counter) as the main galleys, and returns JA's zip. Nothing is saved;
3. EO/typesetter uploads the PDF and EPUB of the translation from the Janeway article manager, and sets the labels
   (`PDF (en)`, `EPUB (en)`; main galleys `PDF (pt)`, `EPUB (pt)`).

The same entry point works on papers in production too, to examine the translation galleys before publication
(with simulated publication data).

The end-user procedure is in the specs wiki, page `Translations`, section `Paper`.

## Assumptions

- **Naming convention** (to be enforced at upload by wjs/specs#3398; enforced here by the entry point):
  - the main TeX file is `{CODE}_{id}.tex`, and `spawn.ini` contains `[tex] main={CODE}_{id}.tex`;
  - the translation is `{CODE}_{id}_{lang}.tex` (e.g. `JCOM_1516_eng.tex`).

  This matters: at publication `BeginPublication` injects the publication data into `{CODE}_{id}.tex` only.
- The archive contains exactly two `.tex` files at its root (helper `.tex` files: wjs/specs#3399).
- The translation has the same DOI and publication date as the main paper.
- Papers published before the WJS publication flow (no "Final sources" file) are out of scope.

## Entry point

`GET /plugins/wjs-review-articles/api/v1/article/<int:pk>/translation-galleys/`

- Query parameter `generate` (repeatable, as JA's): galley formats to build. Default `pdf`, `epub`. `html` is
  refused (400): the HTML galley is only the main one.
- Answer `200 application/zip`: JA's answer, as it is (galleys, `figures/`, logs, `galleys-output.srvc_log`).
  `Content-Disposition` filename: `<translation stem>_galleys.zip` (e.g. `JCOM_1516_eng_galleys.zip`).
- Header `X-WJS-Not-Compiled: pdf,epub` (only when relevant): formats that JA returns from the archive instead of
  compiling them (see "Pre-compiled files" below).
- GET because nothing is saved, and so that it works from Swagger UI without a CSRF token.

Errors (JSON envelopes of `api/exceptions.py`):

| Status | Exception | When |
|---|---|---|
| 400 | `BadRequest` | invalid `generate`; archive not a zip; no `spawn.ini` or no `[tex] main`; not exactly two `.tex` at the root; `main=` not in the archive; `main=` is not `{CODE}_{id}.tex` |
| 401/403 | (DRF) | no credentials / not EO nor the paper's typesetter |
| 404 | `ResourceNotFound` | no such paper, or no workflow |
| 409 | `DataIntegrityConflict` | no sources (no "Final sources" / no typesetting files); no DOI; the published main TeX has no publication data (`???`, placeholder DOI); the publication data in the TeX disagree with the DB; paper without issue (simulation) |
| 502 | `GalleyGenerationFailed` | JA unreachable, non-200, or `texdata` without complete `publication_data` |

## Files

!1625 changes `api/logic.py`, `logic__production.py`, `tests/conftest.py`, `tests/test_api.py`. To avoid
conflicts:

- **new** `wjs/plugins/wjs_review/api/logic__translation.py` — business logic;
- **new** `wjs/plugins/wjs_review/tests/test_api_translation.py` — tests, with their own fixtures;
- `api/views.py` — new `TranslationGalleysView`; guard in `ArticleZipView.put` (see below);
- `api/urls.py` — new route `article/<int:pk>/translation-galleys/`, name `article-translation-galleys`;
- `api/exceptions.py` — new `TranslationGalleysPresent` (409);
- `api/const.py` — default formats, allowed formats, header name.

Nothing in `logic__production.py` or `models.py` changes. Some code is copied from there (wjs/specs#3400).

## Business logic: `BuildTranslationGalleys`

```python
@dataclasses.dataclass
class BuildTranslationGalleys:
    workflow: ArticleWorkflow
    formats: list[str]

    def run(self) -> TranslationGalleys: ...
```

`TranslationGalleys` is a small dataclass: `content: bytes`, `tex_name: str`, `language: str`,
`not_compiled: list[str]`.

`run()` raises `ValidationError` with a code (`invalid_request`, `conflict`, `generation_failed`) that the view maps
to 400/409/502. It writes nothing in the DB nor in the filesystem: archives and TeX files are handled in memory
(`BytesIO`). No `transaction.atomic()` is needed, except the rolled-back one of the simulation (step 3).

### 1. `get_sources() -> bytes`

- published paper (`article.stage == STAGE_PUBLISHED`): `workflow.publication_galleys_source_file` (the "Final
  sources" file, as `ArticleZipView.get`); 409 if missing or missing on disk;
- otherwise: `workflow.get_latest_typesetting_assignment(only_completed=False).files_to_typeset.get()`; 409 if there
  is no assignment or no file.

Read `self_article_path()` into memory.

### 2. `find_translation(archive) -> tuple[str, str]` (main, translation)

- open the zip (400 on `BadZipFile`);
- read `spawn.ini` (root) with `configparser`; 400 if missing or without `[tex] main`;
- `.tex` files at the root of the archive; 400 unless exactly two;
- 400 if `main` is not one of them, or if `main != {CODE}_{id}.tex` (naming convention);
- the translation is the other one.

Pure function on the archive's namelist and `spawn.ini` text, to be reused by the upload check of wjs/specs#3398.

### 3. `get_publication_data(archive) -> dict`

**Published paper** — read back the data that WJS injected at publication, do not recompute them:

- POST `{CODE}_{id}.tex` (from the "Final sources") to `{JCOMASSISTANT_URL}texdata` (as
  `synctex/logic.py`, `text/plain`, timeout 10s);
- take `publication_data` from the JSON answer (`date_published`, `year`, `volume`, `issue`, `eid`, `counter`,
  `section`, `doi`). A "partial" answer is accepted only if all these keys are filled in; 502 otherwise;
- 409 if `date_published` is `???` or the DOI is `10.22323/0.00000000` (main file never injected);
- 409 if the stable fields disagree with the DB: `date_published`, `year`, `volume`, `issue`, `eid`, `section` as
  computed by `workflow.collect_publication_data()` (read-only; `eid` comes from `page_numbers`, so it is stable),
  and the DOI (`article.get_doi()`). The `counter` is not compared (see below).

Why not from the DB: `collect_publication_data()` computes `counter` as 1 + the papers of the same issue (and
section) in `PUBLICATION_IN_PROGRESS`/`PUBLISHED`. At publication the paper is still `READY_FOR_PUBLICATION`, so the
counter is right; once published, the paper counts itself (and any paper of the same issue published later), so
the counter is off by at least one. `counter` goes into `\publicationData{vol}{issue}{section}{counter}`, so the
translation would differ from the main galley.

**Paper not yet published** — simulate, as `SimulatePublicationIdentifiers`:

- inside `transaction.atomic()` + `transaction.set_rollback(True)`, on a fresh copy of the workflow;
- `article.date_published = now()` if unset; 409 if no primary issue (`MissingIssueError`);
- `workflow.collect_publication_data()`, plus `doi = article.get_doi()` (409 if missing).

These are a preview: date and counter are decided at publication.

### 4. `inject(tex_name, tex_bytes, publication_data) -> bytes`

POST to `{JCOMASSISTANT_URL}pubdata` with the same payload as `BeginPublication._prepare_source()`:

```python
{"publication_data": {"date_published": ..., "year": ..., "volume": ..., "issue": ..., "eid": ...,
                      "section": ..., "counter": ..., "doi": ...}}
```

file `(tex_name, tex_bytes, "text/plain")`, timeout 15s (as `_jcom_assistant_injection`). 502 on non-200.

### 5. `build_archive(archive, translation, injected_tex) -> bytes`

New in-memory zip:

- all the entries of the original archive, except `spawn.ini` and the translation `.tex`;
- the injected translation, under its own name;
- a new `spawn.ini`: the original one (all sections kept) with `[tex] main=<translation>`.

### Pre-compiled files

JA treats an output file already in the archive as produced (`Spawn.is_produced()` → `ensure_produced()` skips the
generator): if the archive contains `{translation stem}.pdf`, JA returns it as it is, without compiling and without
the injected publication data.

This is kept on purpose, as a safety net: if JA cannot compile the translation, the typesetter can compile it by
hand and still use the entry point. But the caller must know it:

- before calling JA, look in the archive for `{stem}.{ext}` for each requested format (`pdf`, `epub`), and also for
  the intermediate files that JA would reuse (`{stem}.html`, `.json`, `.opf`, `.xhtml`, which feed the EPUB);
- for each format found (or fed by a found intermediate), log a warning and list it in the `X-WJS-Not-Compiled`
  header: "taken from the archive, not compiled: publication data not injected".

Typesetters are instructed not to include compiled files of the translation.

### 6. `request_galleys(archive) -> bytes`

POST to `{JCOMASSISTANT_URL}galleys?generate=<fmt>&...` with `("{CODE}_{id}.zip", bytes, "application/zip")`, as
`JcomAssistantClient`, no timeout (as `JcomAssistantClient` and `PUT /zip/`). 502 on non-200 or connection error.

`JcomAssistantClient` is not reused: it needs a stored file or a `Path`, and on non-200 it reads
`archive_with_files_to_process.article`, which a `Path` does not have.

### 7. Language

From the `\publicationLanguage{...}` of the translation TeX (regex), else from the translation file name
(`{CODE}_{id}_{lang}.tex`). Kept in the result for logging; the file name of the answer uses the translation's
stem.

## Guard on `ArticleZipView.put`

`PUT /article/<pk>/zip/` regenerates all the galleys with `AttachGalleys`, which keeps one galley per format and
deletes the others (`discard_galleys()`): the translation galleys uploaded by hand would be lost.

In `ArticleZipView.put` (`api/views.py`, not touched by !1625), right after `get_article()` and before reading the
body:

- group `article.galley_set` by the lower-cased extension of `galley.file.original_filename`;
- if any extension appears more than once, raise `TranslationGalleysPresent` (409, code
  `translation_galleys_present`). Message:

  > This paper has translation galleys (more than one galley of the same type), and this entry point cannot
  > regenerate them: they would be lost. Workaround: download the translation galleys, remove them from the article
  > manager, retry this request, then upload the translation galleys again from the article manager. Remember to
  > set the labels again (e.g. `PDF (en)`, and `PDF (pt)` for the regenerated main galley).

- add `409: ErrorSerializer` to its `@extend_schema`.

## View: `TranslationGalleysView`

```python
class TranslationGalleysView(LoggedRequestMixin, EOOrTypesetterAccessMixin, APIView):
    content_negotiation_class = IgnoreAcceptHeaderNegotiation
```

- `get_object_or_404(Article, pk=pk)` + `self.check_object_permissions()` (not `PublishedArticleAccessMixin`:
  unpublished papers are allowed);
- validate `generate` against the allowed formats (`pdf`, `epub`);
- run `BuildTranslationGalleys`, map the error codes to the exceptions above;
- `HttpResponse(content, content_type="application/zip")` with `Content-Disposition` and, if any,
  `X-WJS-Not-Compiled`;
- `@extend_schema` as `ArticleZipView.get`: `(200, "application/zip")` binary, plus the error codes, and the
  `generate` query parameter.

## Tests (`tests/test_api_translation.py`)

JA is mocked (`requests` mocked per URL; or the dummy JA server once !1625 is merged). Fixture archive: `X.tex`
(main), `X_eng.tex`, `spawn.ini` with `main=X.tex`, `X.bib`, `figures/a.png`.

- `find_translation`: ok; no `spawn.ini`; no `main`; one `.tex`; three `.tex`; `main=` missing from the archive;
  `main=` not `{CODE}_{id}.tex`.
- published paper: data from `texdata` of the main file (counter included), not from the DB; injected into the
  translation; the archive sent to `galleys` has `main=X_eng.tex`, the injected translation, the other files and
  the other `spawn.ini` sections; no new `core.File`/`Galley` rows, no new files in the article folder.
- published paper, main file with `???` or placeholder DOI → 409; DOI/date/eid disagreeing with the DB → 409.
- paper in production: simulated data; nothing left in the DB (date_published, page_numbers, identifiers).
- `X_eng.pdf` in the archive → `X-WJS-Not-Compiled: pdf`; not present → no header.
- `generate=html` → 400; JA 500 on `pubdata`/`galleys` → 502.
- permissions: anonymous → 401; author → 403; the paper's typesetter and EO → 200.
- `PUT /zip/` guard: paper with `PDF` and `PDF (en)` → 409, galleys and sources untouched; one galley per type → as
  before.

## Out of scope / follow-ups

- naming convention and upload check: wjs/specs#3398;
- helper `.tex` files: wjs/specs#3399;
- refactor of the duplicated JA calls after !1625: wjs/specs#3400;
- JA building translations in the same request (to be confirmed with Elia in #990);
- automatic labels with the language for the main galleys.
