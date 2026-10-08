# Article → IOP/Editorial-Manager metadata XML export

## Context

Two sample XML files surfaced in `wjs/jcom_profile/tests/aux/` (committed alongside this document at
the time; since removed — see *Sample files* at the end of this document). Both were
Editorial-Manager/IOP-style per-article metadata exports — one a placeholder-heavy template, the
other real-looking data (JINST) that is the better shape reference. Neither file is referenced by any
existing code: they were untracked, no import/export command reads their tag vocabulary
(`ms_no`, `cd_code`/`cd_value`, `author_seq`, `fundref_information`, ...), and it doesn't match the
existing wjApp importers' vocabulary (`preprintid`, `actHistCod`, ...) or mechanism (those connect
directly to wjApp's MariaDB; they don't parse an XML export at all).

**Direction**: this is an **export** — Janeway `Article` (and related models) → this XML shape — not
an import. The two management commands under `wjs/jcom_profile/management/commands/
import_articles_from_wjapp*.py` already recognize `JCAP` among the journals importable from wjApp
(`journal_code in ("JCOM", "JCOMAL", "JHEP", "JCAP")`), but that is a separate, DB-driven mechanism
unrelated to this export.

**Goal**: produce this XML for a given `Article`, for IOP-adjacent journals (JCAP, JINST, ...), most
likely to hand off metadata to a downstream production/indexing system that expects the
Editorial-Manager export shape.

## Mapping specification

Every mapping below was settled through a field-by-field review against both sample files and the
actual Janeway model source (`submission/models.py`, `core/models.py`, `journal/models.py`,
`identifiers/models.py`, `review/models.py`, and the `wjs_review`/`wjs_submission` plugins). Where a
field has no confirmed source, it is called out explicitly as deferred — treat those as open decisions,
not settled behavior to copy blindly.

**Important — null-safety**: the exported XML must never contain the literal text `"None"`,
unless a specific rule above explicitly mandates it (none currently do). Several fields below read
straight from a Django model attribute that is `null=True` (confirmed so far: `Article.subtitle`,
`Section.name` — check the source model before assuming any other field is safe, including ones
reached through a `None`-guarded relation, e.g. `article.section.name` is **not** made safe by
`if article.section`, since `section` can exist with a `None` name). Django's template engine
renders a `None` context value as the literal string `"None"` (via `force_str`), not as an empty
string — `{{ value }}` alone does **not** protect against this. The fix belongs at the point a
model attribute is first read into a presenter property / DTO field (`presenters.py` /
`mappers.py`), coalescing with `or ""` right there — not as a blanket template-level filter
(`|default_if_none:""` on every `{{ }}`), which would hide the same bug at every future call site
instead of fixing it once at the source. The carried-forward DTO+mappers prototype follows this rule;
see `mappers.py`'s `build_article_export_dto`/`map_custom_fields` for the pattern.

**Settled — mapper error policy**: mapper functions **degrade gracefully**, never raise, on
missing/unexpected data (e.g. no `article.manuscript_files` entries, a required custom field's source
being empty where the rule above doesn't already allow it). Emit the best-effort XML (empty
element/attribute, empty file group, etc.) rather than failing the whole export. This applies
repo-wide across `mappers.py` — no per-field exceptions to this rule unless stated otherwise.

### Quick-reference table

| Janeway source | XML target | Notes |
|---|---|---|
| `Article.language` (ISO 639-2/T alpha-3, e.g. `"eng"`) | `article/@lang` | converted to alpha-2 via `pycountry.languages.get(alpha_3=...).alpha_2`, the same lookup `wjs_tags.language_alpha2()` wraps |
| `article.articleworkflow.preprint_id` (settled — always, no `Identifier`/wjApp fallback) | `article/@ms_no`, `history/ms_id/@ms_no` | wjApp-imported articles are out of scope for this export; the `preprintid` `Identifier` path is not used |
| — fixed literal from a per-journal setting, default `"2"`, no per-journal override defined yet | `article/@rev` | decoupled from `history/ms_id/rev_id`; not derived from the article at all |
| count of the article's `EditorDecision` rows with `decision=MAJOR_REVISION` | `history/ms_id/rev_id` | `0` if the article has never had a major revision; independent of `revised_date` below, which still uses the newest one |
| — (generation time) | `article/@export_date` | not sourced from `Article` |
| — | `article/@external_id` | always empty |
| `article.journal.publisher` | `journal/publisher_name` | settings-backed property (`general.publisher_name`) |
| `article.journal.name` | `journal/full_journal_title` | settings-backed property (`general.journal_name`) |
| `article.journal.code` | `journal/journal_abbreviation`, `article_id_list/article_id[@id_type="pii"]` | same value, used in two places |
| `article.journal.print_issn` | `journal/issn[@issn_type="print"]` | settings-backed property |
| `article.journal.issn` | `journal/issn[@issn_type="digital"]` | settings-backed property |
| `article.stage == STAGE_ACCEPTED` | `article_status/@decision_status="accept"`, text `"Accepted"` | settled: only supported case; export raises for any other stage instead of emitting empty XML |
| `article.title` | `article_title` | |
| `article.subtitle` | `article_sub_title` | deprecated field, expected empty |
| `article.section.name` | `publication_type` | **re-settled (IOP feedback)**: no longer also duplicated into a `custom_fields[cd_code="Section"]` entry — `<publication_type>` alone already captures the article type |
| position (1..N) in `article.frozenauthor_set.order_by("order")` (**re-settled, IOP feedback** — not the raw `frozen_author.order` value) | `author/@author_seq` | one `<author>` per `article.frozenauthor_set.order_by("order")`; `order` itself can have gaps, `@author_seq` may not |
| `frozen_author.author_id == article.correspondence_author_id` | `author/@corr` | `"true"`/`"false"` |
| `frozen_author.pk` (always — settled, not `frozen_author.author_id`) | `author/@user_id` | avoids leaking a linked `Account`'s real user id / any collision risk from mixing ID spaces |
| `frozen_author.name_prefix`, or the fixed literal `"Dr."` if empty (**settled**) | `author/salutation` | an empty tag fails ingestion (IOP feedback); `"Dr."` is the agreed fallback |
| `frozen_author.first_name` / `.middle_name` / `.last_name` | `author/first_name`, `middle_name`, `last_name` | |
| `frozen_author.frozen_email` or `.author.email` | `author/email[@addr_type="primary"]` | |
| `frozen_author.frozen_orcid` | `author/orcid` | |
| `controlled_affiliation.organization` (or, for the **corresponding author of a TA article**, `article.submission_data.affiliation.organization` — **re-settled, IOP feedback**) | `author/affiliation/inst` | `str()`; primary affiliation only; **IOP feedback**: required (non-empty) for the corresponding author |
| `controlled_affiliation.department` | `author/affiliation/dept` | |
| `controlled_affiliation.title` | `author/affiliation/person_title` | |
| `controlled_affiliation.organization.location.name` | `author/affiliation/city` | **IOP feedback**: required (non-empty) for the corresponding author |
| `controlled_affiliation.organization.location.country` | `author/affiliation/country` | `str()`; **IOP feedback**: required (non-empty) for the corresponding author |
| `article.manuscript_files.all()` (settled — **not** `article.galley_set`) | `file_list/file` ("Complete Document for Review (PDF Only)") | at "accepted" stage there generally are no post-typesetting galleys yet; the submitted manuscript file(s) are what's sent |
| `article.source_files` only (settled — **not** `+ article.supplementary_files`) | `file_list/file` ("Source Files (incl. Word, TeX, Figures, etc)") | `supplementary_files` are ESM, not source files per `file_designation`; dropped from this group |
| editor decision date of the article's latest **major** revision | `history/revised_date` | empty if no major revision has ever been requested (settled — no fallback) |
| date the author submitted that same latest major revision | `history/submitted_date` | empty if no major revision has ever been requested (settled — no fallback to `article.date_submitted`) |
| `article.date_submitted` | `history/received_date` | fixed at first-ever submission, never overwritten |
| `article.date_accepted` | `history/decision_date` | re-settled (MR !1487 review) — always set by export time, since this export only fires for accepted articles |
| `article.abstract` | `abstract` | one-line text, no line breaks -- see implementation note below |
| `article.submission_data.access_mode.code` against a per-journal allowlist | `custom_fields[cd_code="OA Agreed"]` | `"Yes"`/`"No"`; default `["open-access"]`, no per-journal override defined yet |
| `article.date_accepted` (only if OA Agreed = Yes) | `custom_fields[cd_code="OA date requested"]` | acceptance date, not submission date; formatted `DD-Mon-YYYY`; **re-settled (IOP feedback)**: the entire `<custom_fields>` entry is **omitted**, not just empty-valued, when OA Agreed = No |
| `article.license.short_name` if OA Agreed=Yes else omitted entirely (OA licence type) / `"Open Access"` if OA Agreed=Yes else `"Standard"` (Copyright/Licence Type) | `custom_fields[cd_code="OA licence type"]` and `[cd_code="Copyright/Licence Type"]` | **re-settled (IOP feedback)** — `"OA licence type"` is also omitted (not just empty) when OA Agreed = No; `"Copyright/Licence Type"` is now constrained to IOP's own preset vocabulary — see detail below |
| — (deferred) | `custom_fields[cd_code="Production Comments"]` | always empty |
| `article.primary_issue` where `issue_type.code == "collection"` | `custom_fields[cd_code="Special Issue"]` | `cd_name`=`Issue.issue_title`, `cd_value`=`Issue.short_name` |
| `article.keywords.all()` | `content/attr_type[@name="Keywords"]/attribute` | `@id`=`keyword.pk`, `@name`=`keyword.word` |
| `article.total_pages`, computed and persisted onto that field during mapping if not already set | `content/total_pages_calc` | **settled**: page-counting (e.g. via `pdfinfo`) happens once, during mapping, with the result written back to `Article.total_pages` — not recomputed on every export |
| `article.articlefunding_set` empty? | `fundref_information/no_funders` | `"True"`/`"False"` |
| `ArticleFunding.name` / `.fundref_id` / `.funding_id` | `fundref_information/funder/preferred_label` / `concept_id` / `grant_number` | one empty `<funder>` if no rows, matching both samples |

### Document envelope

**Settled (IOP feedback)**: the rendered XML must be wrapped in three lines IOP's ingestion requires
and the original template was missing entirely:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--sl.dtd v.4.28.5-->
<!DOCTYPE article_set SYSTEM "sl.dtd">
<article_set dtd_version="4.28.5">
<article ...>
  ...
</article>
</article_set>
```

The `<?xml ?>` declaration was already there; the `<!--sl.dtd-->` comment, the `<!DOCTYPE>`, and the
`<article_set dtd_version="...">`/`</article_set>` wrapper around the (single) `<article>` element
were not. Not sourced from `Article` — a fixed literal matching IOP's current DTD version.

### Root `<article>`
- `@export_date`: generation timestamp, not sourced from `Article`.
- `@external_id`: `""` (always empty for now).
- `@lang`: `Article.language` (ISO 639-2/T alpha-3, e.g. `"eng"` — confirmed via Janeway's own
  `LANGUAGE_CHOICES` in `submission/models.py` and WJS's `SUBMISSION_ARTICLE_LANGUAGES` override in
  `wjs/defaults/settings_submission.py`, both alpha-3) converted to alpha-2 for the export via
  `pycountry.languages.get(alpha_3=...).alpha_2` — the same lookup `wjs_tags.language_alpha2()` wraps
  (see also its inline use in `synctex/forms.py`/`import_utils.py`). Confirmed against both sample
  fixtures, which show `lang="EN"` (alpha-2).
- `@ms_no`: **settled** — always `article.articleworkflow.preprint_id`
  (`wjs_review.models.ArticleWorkflow`, a `@property` — `f"{article.journal.code}_{article.pk}"`, e.g.
  `"JCAP_1234"`). The earlier draft's `Identifier(article=article, id_type="preprintid").identifier`
  path (which mirrors wjApp's own `preprintid` convention, e.g. `"JCOM_010A_0324"`) is **not used at
  all**: wjApp-imported articles are not a concern for this export, so there's no need to reconcile the
  two different shapes or to inform IOP of a format change — there's only ever one format now. There is
  no dedicated "manuscript number" field on `Article` itself, hence going through `ArticleWorkflow`.
- `@rev`: **settled** — a fixed literal (the XML schema/format version) sourced from a per-journal
  setting, `{journal_code: value, ..., None: "2"}` (same shape as `wjs_submission.settings`'s
  `DEFAULT_OA_MESSAGE_CODES`/`OA_MESSAGE_CODES` pattern), default `"2"`, no per-journal override
  defined yet. **Not** derived from the article at all, and **not** the same source as
  `history/ms_id/rev_id` (that one is a count of the article's major revisions, described
  below).

### `<journal>`
- `publisher_name` ← `article.journal.publisher` (settings-backed property, `general.publisher_name`)
- `full_journal_title` ← `article.journal.name` (settings-backed property, `general.journal_name`)
- `journal_abbreviation` ← `article.journal.code`
- `issn[@issn_type="print"]` ← `article.journal.print_issn`
- `issn[@issn_type="digital"]` ← `article.journal.issn`

None of `publisher_name`/ISSN are plain model fields — all three are Django-settings-backed properties
on `journal.Journal`, resolved per journal via `setting_handler`, not database columns.

**IOP feedback**: flagged `full_journal_title`/`issn[@issn_type="digital"]` as not carrying correct
data in a sample export. Confirmed **not a mapper bug** — `map_journal` already reads the right
settings-backed properties; the reported values were stale/placeholder journal configuration in the
environment the sample was generated from, fixed by correcting that journal's settings data, not the
code.

### `<article_status>`
**Settled**: this export only ever fires for accepted articles — there is no other supported entry
point. When `article.stage == submission.models.STAGE_ACCEPTED`, emit `@decision_status="accept"` and
text `"Accepted"`; for any other stage, **raise an exception** rather than emit empty/best-effort XML.
This is a deliberate carve-out from the general "mappers degrade gracefully, never raise" policy (see
the null-safety note in *Mapping specification* above): that policy covers missing/unexpected *data* on
an otherwise-valid accepted article, not being invoked against an article in the wrong stage at all —
the latter is a precondition violation, not a data-completeness gap, and should fail loudly. No other
`decision_status` vocabulary is needed — reject/revise/pending and other stages are genuinely
unreachable by design, not just unmapped.

### Title / section
- `<article_title>` ← `article.title`
- `<article_sub_title>` ← `article.subtitle` (deprecated field since Janeway 1.4.2, expected empty).
  **Implementation note**: `Article.subtitle` is `null=True` — coalesce to `""` (see the
  null-safety note above).
- `<publication_type>` ← `article.section.name`. **Implementation note**: `Section.name` is
  `null=True` even when `article.section` is set — `if article.section` alone does not make this
  safe; coalesce `article.section.name` itself too (see the null-safety note above). **Re-settled
  (IOP feedback)**: this value is no longer *also* duplicated into a `"Section"` `<custom_fields>`
  entry — see the `<custom_fields>` section below.

### `<author_list><author>` — one per `article.frozenauthor_set.order_by("order")`
- `@author_seq` ← **re-settled (IOP feedback)**: the author's **1-indexed position** in
  `article.frozenauthor_set.order_by("order")`, not the raw `frozen_author.order` value. `order` has
  no DB constraint keeping it gap-free (author reordering/removal can leave e.g. `1, 3, 4`), and IOP
  requires `@author_seq` to be consecutive. `order` still decides the *ordering*; it no longer supplies
  the *value*.
- `@corr` ← `"true"` if `frozen_author.author_id == article.correspondence_author_id` (both non-null),
  else `"false"`
- `@user_id` ← `frozen_author.pk`, always — **settled**: not `frozen_author.author_id`/linked
  `Account` id in any case. Rationale: an `Account`'s real id means nothing to IOP and risks colliding
  with a different ID space than `FrozenAuthor.pk`'s own
- `salutation` ← `frozen_author.name_prefix`, or the fixed literal `"Dr."` if empty (**settled**):
  an empty tag fails ingestion (IOP feedback), and `name_prefix` is often blank.
- `first_name` / `middle_name` / `last_name` ← the matching `FrozenAuthor` fields
- `email[@addr_type="primary"]` ← `frozen_author.frozen_email`, falling back to
  `frozen_author.author.email` if a linked account exists
- `comments` — always empty
- `orcid` ← `frozen_author.frozen_orcid`
- `affiliation[@seq="1"]` — **only the primary/first affiliation; multiple affiliations are out of
  scope for this first pass.** Resolved via
  `frozen_author.controlledaffiliation_set.filter(is_primary=True).first() or
  frozen_author.controlledaffiliation_set.first()` (`core.models.ControlledAffiliation`, which replaced
  the old flat `institution`/`department`/`country` fields as of Janeway 1.8) — **except** for one
  narrow, **re-settled (IOP feedback)** case below. This is each individual author's own affiliation
  (per-`FrozenAuthor`), not a single article-level/paper affiliation.

  **TA institution (re-settled, IOP feedback, narrower than the earlier retracted proposal)**: an
  earlier review comment suggesting `article.submission_data.affiliation` as a blanket substitute for
  every author's own affiliation was retracted — that stands for the general case. IOP has since
  clarified that for a **transformative-agreement** article
  (`article.submission_data.access_mode.code == "oa-transformative-agreement"`), the reported
  institution must specifically be *the one eligible for the TA*, which need not be whichever
  `ControlledAffiliation` happens to be marked primary on the corresponding author's own record. For
  the **corresponding author only**, on a TA article, when `article.submission_data.affiliation` is
  set, that `ControlledAffiliation` is used instead of the per-author lookup above. Every other
  author, and every non-TA article, is unaffected.

  **Corresponding-author completeness (IOP feedback)**: IOP requires `inst`/`city`/`country`
  non-empty for the corresponding author specifically — an empty value means their team has to
  populate it manually from the reference PDF. `ControlledAffiliation.organization` is nullable (and
  even when set, `Organization.location` can itself be unset), so this isn't guaranteed by the model.
  **Settled**: rather than fabricate data that doesn't exist, `SendProductionXMLToPublisher` checks
  the corresponding author's mapped affiliation when building the export zip and, if `inst`/`city`/
  `country` come out empty, logs a **second, separate** EO message (alongside the normal "export
  prepared" one, same hand-written style as `VerifyProductionRequirements._log_acceptance_issues`)
  flagging it for manual fixup in Janeway. Does not block the zip.

  **Investigated and closed, no mapper fix possible (IOP feedback on a real export batch, articles
  5057/5061/5093/5095/5874/5955/5958/5965/5979/5987)**: IOP reported no address/country data for
  any author across that batch, including corresponding authors. A read-only diagnostic
  (`diagnose_export_affiliations`, `management/commands/`) was run against all ten real articles
  and checked every possible source: the `FrozenAuthor`-linked `ControlledAffiliation` rows (what
  the export sees), the linked `Account`'s own live `ControlledAffiliation` rows (both the raw list
  and Janeway's canonical `Account.primary_affiliation()`), and the legacy pre-`FrozenAuthor`
  `article.authors` M2M. **All were empty for every single author on every single article** — this
  is a genuine upstream data gap (no author on this instance has ever had an affiliation recorded
  anywhere Janeway tracks it), not a stale snapshot and not a divergence from Janeway's own
  `FrozenAuthor.affiliation()`/`ControlledAffiliation.get_primary()` logic, which `map_affiliation`
  matches exactly. No broader fallback in `map_affiliation` would help, since there is no data
  anywhere to fall back to. Analysis suspended (not pursued further per direction) — the
  corresponding-author-completeness EO warning above remains the only mitigation available; a real
  fix would require an upstream data-entry/collection process change, out of this export's scope.
  - `inst` ← `str(controlled_affiliation.organization)`
  - `dept` ← `controlled_affiliation.department`
  - `person_title` ← `controlled_affiliation.title`
  - `addr1`/`addr2`/`addr3` — always empty (not modeled)
  - `city` ← `controlled_affiliation.organization.location.name` (`Organization.location` property →
    first `core.models.Location`)
  - `state` — always empty
  - `country` ← `str(controlled_affiliation.organization.location.country)`
  - `post_code`/`phone`/`fax` — always empty (not modeled)

### `<article_id_list><article_id id_type="pii">`
Text content ← `article.journal.code`. **Not** a separate `Identifier` row — just the journal code,
redundant with `<journal_abbreviation>`.

### `<file_list><file>` — two groups, in this order
1. **"Complete Document for Review (PDF Only)"** — **settled, changed from the original proposal**:
   one `<file>` per `article.manuscript_files.all()` entry, not `article.galley_set` — at "accepted"
   stage there generally are no post-typesetting galleys yet (`galley_set` would likely be empty), so
   the submitted manuscript file(s) are sent instead. `file_name`/`file_originalname` ←
   `manuscript_file.original_filename`; `file_format`/`file_extension` derived from that filename's
   extension (not a constant — the earlier draft's examples hardcoded `"pdf"` for every entry, which
   the reviewer flagged as wrong even for this group, let alone for source files).
2. **"Source Files (incl. Word, TeX, Figures, etc)"** — **settled, changed from the original
   proposal**: one `<file>` per entry in `article.source_files.all()` (`core.File`) only.
   `article.supplementary_files` (`core.SupplementaryFile`, aka ESM) is **dropped from this group** —
   supplementary/electronic-supplementary-material files are not "source files" per the schema's own
   `file_designation` semantics, and mixing them in was a bug in the original mapper draft.
   `file_format`/`file_extension` derived from the filename's extension, since `core.File` isn't a
   `Galley` and has no `.type`.
- `file_caption`/`file_tag`/`attribute`/`num_pages_calc`/`num_pages_actual` — always empty.
- `file_category`: `""`; `submission_medium`: `"online"` (both constant, matching the samples).

### `<history><ms_id ms_no="...">`
- `@ms_no` — same value as the root `article/@ms_no`.
- `rev_id` — **re-settled (MR !1487 review)**: a straight **count** of the article's
  major-revision `EditorDecision` rows; `0` if the article has never had one. Supersedes the
  earlier "`review_round.round_number` of the newest major-revision `EditorDecision`" approach,
  which over-counts whenever a minor/technical revision round falls between two major-revision
  rounds (e.g. a minor revision in round 1 followed by a major revision in round 2 would report
  `rev_id=2` even though it's the article's first major revision). **Not** the same source as
  `article/@rev` any more — that one is now a fixed per-journal-setting literal, see the root
  `<article>` section above.
- `revised_date` ← the **decision date of the article's newest (latest-created, by
  `EditorDecision.created`) major-revision `EditorDecision`** (independent of `rev_id` above, which
  no longer selects a single row), if any (internal decision — supersedes the earlier "latest
  `RevisionRequest.date_requested`" rule, which didn't
  distinguish major from minor/technical revisions). Empty if the article has never had a major
  revision. IOP indicates only one `revised_date` is expected even with multiple major revisions —
  the newest one wins, earlier ones are dropped.
- `submitted_date` ← the **date the author submitted that same latest major revision**, if any
  (internal decision, same scoping as `revised_date` above). **Settled**: no fallback to
  `article.date_submitted` — stays **empty** whenever the article has never had a major revision
  (straight accept, or only minor/technical revisions along the way), same as `revised_date`. This
  drops the old rule's round-1 fallback entirely.
- `received_date` ← always `article.date_submitted` — the article's original, first-ever submission
  date. Confirmed to be written exactly once in Janeway core
  (`submission/views.py`, at initial submission) and never overwritten by later revisions.
- `decision_date` ← plain **`article.date_accepted`** (**re-settled, MR !1487 review** — supersedes
  the earlier `EditorDecision`-based lookup above/below in this doc's history). This export only
  ever fires for accepted articles, so `date_accepted` is always set by then and `date_declined` is
  always `None` (`Article.accept_article()` clears it on every acceptance) — the `EditorDecision`
  lookup, with its own `date_accepted`/`date_declined` fallback for the no-matching-row case, was
  needless complexity for a value `accept_article()` already stamps on the article itself.
- Each date is rendered as `<year>/<month>/<day>` sub-elements, **plain `str(int)`, no leading zeros**
  (e.g. month `7`, not `07`) — confirmed against both sample files, which never zero-pad these.
- Single-value date fields elsewhere in the document (e.g. "Date OA Requested") use a different,
  separate format: `DD-Mon-YYYY` (e.g. `"02-Jul-2026"`).

### `<abstract>` ← `article.abstract`

**Implementation note**: must be rendered as one-line text, no line breaks.
`Article.abstract` is a `JanewayBleachField` and can contain literal newlines (a plain-text
textarea) or block-level HTML (`<p>`/`<br>`) that a browser renders as line breaks — collapse any
run of whitespace containing a line break (and any repeated plain whitespace) to a single space,
then strip. This normalizes whitespace only; it does not strip HTML tags, which is a separate,
not-yet-requested concern. Implemented as `formatters.to_single_line` (DTO+mappers prototype), applied at the point `abstract`
is produced (`mappers.py`'s `build_article_export_dto`).

**Settled**: in addition to single-lining, `abstract` must be **XML-escaped** (`&`, `<`, `>`, and
friends → entities) so that any literal HTML markup surviving in `Article.abstract`
(a `JanewayBleachField`) can't break the exported document's well-formedness. HTML tags are still not
*stripped* — only escaped — so a `<p>` in the source shows up as literal `&lt;p&gt;` text in the XML,
not as removed markup or as broken XML.

### `<configurable_data_fields><custom_fields>`
The JINST sample originally had **8** `<custom_fields>` elements. **Re-settled (IOP feedback on a
real export batch)**: two of those eight — `"Section"` and `"Additional Authors"` — are dropped
entirely, since both duplicated information already present elsewhere in the export (see the two
rules that used to be numbered 6 and 7 below). The rule set below now produces **6** elements when
OA is agreed, **4** when it isn't (rule 3 still covers two elements at once when OA is agreed).

1. `cd_code="OA Agreed" cd_name="OA Requested?"` — `cd_value` = `"Yes"` if
   `article.submission_data.access_mode.code` (from the `wjs_submission` plugin's `ArticleSubmission` /
   `AccessMode` models, reachable as `article.submission_data.access_mode.code`) is a member of a
   per-journal-configurable allowlist of OA-mode codes, else `"No"`. If `article.submission_data`
   doesn't exist or has no `access_mode`, treat as `"No"`. **Settled**: default `["open-access"]`, no
   per-journal override defined yet. Implemented in `wjs_review/metadata_export/mappers.py`
   (`DEFAULT_OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE = {None: ["open-access"]}`, overridable via
   the `REVIEW_METADATA_OA_AGREED_ACCESS_MODE_CODES_BY_JOURNAL_CODE` Django setting) — same
   `{journal_code: [access_mode_code, ...], None: [...]}` shape as `wjs_submission.settings`'s
   `DEFAULT_OA_MESSAGE_CODES`/`OA_MESSAGE_CODES` pattern, but defined locally rather than reusing
   `OA_MESSAGE_CODES` itself (that question was flagged as worth a look but not settled; the two
   dicts stay separate for now).
2. `cd_code="OA date requested" cd_name="Date OA Requested"` — `cd_value` = `article.date_accepted`
   (the acceptance date, **not** the submission date — Janeway has no literal "OA requested on"
   timestamp, so the acceptance date is used) formatted `DD-Mon-YYYY`, only if field 1 above is
   `"Yes"`. **Re-settled (IOP feedback)**: when field 1 is `"No"`, this `<custom_fields>` entry is
   **omitted from the XML entirely** — not emitted with an empty `cd_value`.
3. `cd_code="OA licence type" cd_name="OA Licence Type"` **and**
   `cd_code="Copyright/Licence Type" cd_name="Copyright/Licence Type"` — **re-settled (IOP feedback),
   no longer the same value**:
   - `"OA licence type"` ← `article.license.short_name` if set else `""`, only if field 1 above
     (`"OA Agreed"`) is `"Yes"`. **Re-settled (IOP feedback)**: when field 1 is `"No"`, this
     `<custom_fields>` entry is **omitted from the XML entirely**, same rule as "OA date requested"
     above.
   - `"Copyright/Licence Type"` ← `"Open Access"` (fixed literal) when field 1 above (`"OA Agreed"`)
     is `"Yes"`; otherwise the fixed literal `"Standard"`. **Re-settled (IOP feedback)** — resolves
     the "unconfirmed" vocabulary question below: IOP confirmed this field accepts only a small
     preset vocabulary, not an arbitrary `license.short_name`. This is a behavior change from the
     original rule (which fell back to `license.short_name`, e.g. `"CC BY 4.0"`, in the non-agreed
     case) — that no longer matches the JINST sample's `"Open Access"` value either way, since IOP's
     confirmed vocabulary doesn't include arbitrary license short names at all.
   - **Resolved**: the real-data validation pass mentioned in an earlier draft of this doc
     (`wjs/specs#3126`) reported this field might be a fixed controlled vocabulary rather than
     `license.short_name`; that speculation is now confirmed directly by IOP, with the actual
     vocabulary being `"Standard"` / `"Open Access"` / (a third, non-standard-copyright case that IOP
     will notify the journal about directly once an author has signed the relevant form — not
     something this mapper generates).
4. `cd_code="Production Comments" cd_name="Production Comments"` — `cd_value` always `""`. Explicitly
   deferred; do not populate from any field.
5. `cd_code="Special Issue" cd_name="Special Issue Title"` — only if `article.primary_issue` is set
   *and* `article.primary_issue.issue_type.code == "collection"` (matches the `SpecialIssueFactory`
   convention already in `jcom_profile/factories.py`): override `cd_name` with that `Issue.issue_title`,
   `cd_value` with that `Issue.short_name`. Otherwise `cd_name` stays literally `"Special Issue Title"`,
   `cd_value` empty.

**Dropped (re-settled, IOP feedback on a real export batch)**: this rule set used to include two more
entries, `cd_code="Section"` (`cd_value` = `article.section.name`, the same source `<publication_type>`
already uses) and `cd_code="Additional Authors"` (`cd_value` = every `FrozenAuthor` after the first,
joined with `", "`, duplicating the individually-listed `<author_list>` entries). Both are gone —
neither adds information the export doesn't already carry elsewhere.

### `<content>`
- `attr_type[@id="23214610" @name="Keywords"]` — one `<attribute>` per `article.keywords.all()`, with
  `@id=str(keyword.pk)`, `@name=keyword.word`, text `"selected"`.
- `total_pages_calc` ← `str(article.total_pages)`. **Settled**: if `article.total_pages` isn't already
  set, the mapper computes it (e.g. via `pdfinfo` against the manuscript) and **persists it onto
  `Article.total_pages`** at mapping time, rather than leaving it empty or recomputing on every future
  export — the mapping step gets a documented, deliberate write side-effect here, which is otherwise
  unusual for this codebase's mappers (worth calling out explicitly wherever `run()`/mapper
  responsibilities are documented in the eventual implementation).
- `total_pages_actual`, `total_tables`, `num_color_figures`, `num_bw_figures`, `total_figures`,
  `total_pdf_pages` — always empty; nothing in Janeway's schema models these.

### `<forms/>` — always empty.

### `<fundref_information>`
- `no_funders` ← `"True"` if `article.articlefunding_set.all()` is empty, else `"False"`.
- `<funder>` — one per `submission.models.ArticleFunding` row: `preferred_label`=`funder.name`,
  `concept_id`=`funder.fundref_id`, `grant_number`=`funder.funding_id`. If there are no rows, still
  emit exactly one empty `<funder>` element, matching both samples' shape when `no_funders=True`.

## Architecture evaluation

Three routes were considered for turning an `Article` into this XML, with one hard constraint from the
outset: **no procedural building of the XML that mixes data-extraction logic and tree-construction in
the same functions.**

| | DRF serializers → XML renderer | Dataclasses → Django template | Hand-rolled DTO + Visitor/Builder |
|---|---|---|---|
| Separates logic from representation? | Partially — `to_representation` mixes "what data" with "what shape"; a *custom* renderer is still needed for structure | Yes — DTO/presenter = data, template = shape | Yes, but reinvents what Django templates already give for free |
| Handles this schema's XML **attributes** (`@author_seq`, `@corr`, `@cd_code`, `@issn_type`, ...)? | No out of the box — DRF ships no XML renderer at all (`djangorestframework-xml` isn't installed in this project), and generic renderers only produce nested elements, not attributes; a custom renderer is most of the real work anyway | Trivial — a template is just text, `attr="{{ value }}"` costs nothing | Trivial, but at the cost of building an ad hoc templating layer |
| New dependency? | `djangorestframework-xml`, or an equivalent hand-rolled renderer (same cost as the third option) | None — `django.template.loader.render_to_string` | None |
| Matches existing codebase convention? | No precedent in this repo | **Yes** — Janeway's own CrossRef DOI export (`identifiers/logic.py` + `templates/common/identifiers/crossref_*.xml`) already builds a template context (light "bundle" objects such as `article.object.frozen_authors_for_jats_contribs()`) and renders via `render_to_string` | No precedent |
| Testability of mapping logic alone (no XML) | Medium — DRF `.data` still drags in request/context machinery | High | High |
| Testability of XML shape alone (no DB) | Low | High (with a pure-DTO variant) | High |

**Decision**: dataclasses + Django template, on the strength of the existing CrossRef precedent and the
attribute-heavy nature of this schema, which DRF's renderer model isn't built for.

### The remaining fork: DTO+mappers vs. presenter-properties — **RESOLVED: DTO+mappers**

Within "dataclasses + template," a further choice surfaced: keep the dataclasses as **pure data**
(plain values only) with a **separate mapper-function layer** doing the ORM/business-logic lookups, or
let the dataclasses **wrap the live model instances** and expose the mapped values as
`@property`/`@cached_property`. Both were prototyped in full as MRs specifically so the choice could be
made against real code rather than continued discussion (!1487 DTO+mappers, !1488 presenters).

**Decision: DTO+mappers (!1487) carried forward; the presenter prototype (!1488) is dropped and
closed.**

#### Portability rationale (which prototype adapts best to a second publisher)

*The question originally asked, evaluated by reading the prototype code on both branches directly
(`feature/metadata-export-dto-mappers`, `feature/metadata-export-presenters`), not just this
document's summary of them:* suggest the one that will result easier to adapt to a different
publisher; the different publisher could ask for a different list of fields, a different
representation of some fields (e.g. the authors list as a comma-separated string), and it will surely
require a different template (which can probably be ignored).

The design doc's own comparison table (above) has the two prototypes close to a tie on testability and
cohesion. On multi-publisher portability, they are not close:

1. **A different field list is a composition change, not a class change.** In the DTO variant, "which
   fields exist" lives in `dto.py`, and "how each is resolved" lives in ~20 independent free functions
   (`map_ms_no`, `map_journal`, `map_history_dates`, `map_funders`, ...), each of shape
   `article -> value`. `build_article_export_dto` is a thin composition that does no lookups itself. A
   publisher B wanting 12 of these fields plus 3 new ones gets its own DTO and its own
   `build_publisher_b_dto`, calling the *same* mappers à la carte — nothing subclassed, nothing
   overridden, and the JCAP exporter cannot regress from it. In the presenter variant, the field set,
   the mapping logic, and the live `Article` reference are fused into one class; publisher B means
   either subclassing `ArticleExportPresenter` (a shared base that grows into the union of every
   publisher's needs, since properties can be added but not meaningfully removed) or copying the class
   and duplicating all the ORM logic — the subclass-a-business-logic-class pattern
   `.claude/rules/architecture-django.md` explicitly steers away from ("consider extracting the shared
   behaviour into a mixin or a separate helper class... rather than subclassing one business logic
   class from another").
2. **A different representation of a field is a one-line swap at compose time.** The point still
   held when it mattered: `map_custom_fields` used to produce `", ".join(str(fa) for fa in ...)` for
   a comma-separated "Additional Authors" `<custom_fields>` entry while `authors` stayed a list of
   DTOs for `<author_list>` — two shapes of the same underlying `frozenauthor_set`, coexisting because
   the shape is decided *where the DTO is built*, drawing on a pure, publisher-agnostic
   `formatters.py`. (That specific "Additional Authors" field was later dropped as redundant with
   `<author_list>` — see *Re-settled* above — but the architectural point about representation being
   a compose-time decision, not a class-shape decision, is unaffected.) With presenters, a property's return type is fixed on the class —
   two representations means either per-publisher cruft on a shared object, or an override in a
   subclass (inheritance carrying formatting decisions).
3. **It gives a registry seam this repo already uses everywhere.** A per-journal/per-publisher dict —
   `{None: build_iop_dto, "JCAP": build_iop_dto, "XYZ": build_publisher_b_dto}` — is exactly the
   `WJS_ARTICLE_ASSIGNMENT_FUNCTIONS`/`WJS_REVIEW_CHECK_FUNCTIONS` pattern from `CLAUDE.md`'s
   *Multi-journal configuration pattern*. **Now implemented** as `service.PUBLISHER_COMPOSERS`
   (`{journal_code: (compose_function, template_name)}`), with `serialize_article_to_metadata_xml`
   selecting per journal — see *Prototype implementation* below. There is no equivalently clean seam on
   a presenter class.
4. **New templates stay testable without a DB.** Each new publisher brings a new template — where the
   bugs will actually be. The DTO branch's `render_demo.py` renders real DTOs through the real template
   with zero DB, so a new publisher's XML shape is verifiable from hand-written literals. The presenter
   branch had to fall back to `SimpleNamespace` stand-ins, because presenters transitively import
   Janeway model modules that need the full app registry — a structural limitation of that
   architecture's standalone testability, not just an implementation shortcut.

#### The one real cost, and how to pay it — **RESOLVED: build it now**

The DTO variant's downside is duplication: adding a field touches both `dto.py` and `mappers.py`, and
per-publisher DTOs multiply that. Since the mappers are already uniformly `article -> value`, the
mitigation is to make the per-publisher layer a declarative, ordered field spec rather than a
hand-written dataclass plus compose function:

```python
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
```

**Settled — reversing the earlier "defer until a second publisher exists" call: build this now**, even
with only one publisher registered. Concretely:

1. **Every `ArticleExportDTO` field gets its own single-value mapper** (`article -> value`), so the
   field spec above can be a flat `{field_name: mapper_function}` dict with no exceptions. This means
   splitting the handful of current mappers that return more than one field's worth of data, and
   promoting a few inline expressions to named mappers:
   - `map_decision_status_and_label(article) -> (status, label)` → `map_decision_status(article) ->
     str` + `map_decision_label(article) -> str`. Both still key off `article.stage ==
     STAGE_ACCEPTED` and both still raise `UnsupportedArticleStageForExportError` together for any
     other stage — the precondition rule above is unaffected by this split.
   - `map_history_dates(article) -> (received, revised, submitted, decision)` → four separate
     functions, `map_received_date`, `map_revised_date`, `map_submitted_date`, `map_decision_date`,
     each `article -> DateParts`. The private helpers they share
     (`_latest_major_revision_editor_decision`, `_resolve_decision_date`) stay as module-level
     functions each of the four calls independently — this reintroduces the same "small duplicate
     query" shape already accepted for `frozenauthor_set` (see *Practical next steps* below), not a
     new concern to solve.
   - `map_funders(article) -> (funders, no_funders)` → `map_funders_list(article) ->
     List[FunderExportDTO]` + `map_no_funders(article) -> bool`.
   - The four fields currently assembled *inline* inside `build_article_export_dto`
     (`title`, `subtitle`, `publication_type`, `abstract`) become real named mappers — `map_title`,
     `map_subtitle`, `map_publication_type`, `map_abstract` — each just wrapping the one-line
     expression that's there today (e.g. `map_subtitle` is `article.subtitle or ""`), so every field
     in the spec is genuinely `mapper_function(article)`, never a mix of mapper calls and inline
     expressions.
2. **A generic composer replaces the hand-written `build_article_export_dto` body**:
   ```python
   def compose_dto(article, dto_class, field_spec):
       """Build ``dto_class(**{name: mapper(article) for name, mapper in field_spec.items()})``."""
       return dto_class(**{name: mapper(article) for name, mapper in field_spec.items()})
   ```
   `build_article_export_dto(article)` becomes a one-line call — `compose_dto(article,
   ArticleExportDTO, JCAP_FIELDS)` — kept as a named function (not inlined at every call site) so
   existing callers/tests/imports (`mappers.build_article_export_dto`) don't need to change.
3. **`service.PUBLISHER_COMPOSERS`** (already scaffolded, see *Prototype implementation* below) keeps
   its current shape (`{journal_code: (compose_function, template_name)}`) unchanged — it already
   composes at the right level (whole compose function per journal), so it needs no rework for this.
4. **Adding a second publisher** becomes exactly the recipe sketched here originally:
   ```python
   PUBLISHER_B_FIELD_NAMES = ("ms_no", "journal", "authors", ...)
   PUBLISHER_B_FIELDS = {
       **{name: JCAP_FIELDS[name] for name in PUBLISHER_B_FIELD_NAMES},
       # authors as a comma-separated string rather than a list of DTOs
       "authors": map_authors_csv,
   }

   def build_publisher_b_dto(article):
       return compose_dto(article, PublisherBExportDTO, PUBLISHER_B_FIELDS)
   ```
   registered into `service.PUBLISHER_COMPOSERS` under that publisher's journal code(s).
   `map_authors_csv` would be an ordinary named function in `mappers.py`
   (`return join_author_names(map_authors(article))`), not a generic `compose()` helper — greppable,
   gets its own docstring, directly unit-testable. Restating `"authors"` after the `**` unpacking is
   the standard "inherit a dict, override one entry" idiom (later entries win; linters don't flag it,
   unlike a literal duplicate key in one dict literal); overriding an existing key also keeps its
   original position, so if field order ever drives XML element order, publisher B's `authors` stays
   where JCAP had it. `{name: JCAP_FIELDS[name] for name in ...}` raises `KeyError` on a typo, failing
   loudly at import — the desired behaviour, but a deliberate choice, not an accident. `dto_class`
   would typically be a new dataclass (`PublisherBExportDTO`) if the field set/shape genuinely differs
   from `ArticleExportDTO`, or `ArticleExportDTO` itself if it's a strict subset with substitutions (as
   in this sketch).

This is a real, if mechanical, refactor of `mappers.py`'s composition layer — no change to any
individual mapper's *logic*, only to how they're split/named and wired together. Every existing
mapper-level test keeps testing the same behavior (split/renamed mappers get correspondingly
split/renamed tests); the `build_article_export_dto` integration tests, and every `service.py`/
management-command/template-contract test, are unaffected, since none of their public call signatures
change.

#### Practical next steps — resolved

1. ~~Carry !1487 forward; close !1488.~~ **Done.**
2. Two things the presenter branch appeared to do better, both revisited and **not ported**:
   - ~~`functools.cached_property` caching discipline for `_frozen_authors`/`_location` — the mappers
     re-query `frozenauthor_set` in both `map_authors` and `map_custom_fields`.~~ **Settled: left
     as-is.** Free functions have no natural object to cache on the way a presenter's
     `@cached_property` does; threading a precomputed `frozen_authors` list through both mapper
     signatures was considered and rejected — one extra small query per export isn't worth changing
     the mapper contract for. The duplicate query is a deliberate, accepted cost, not a TODO.
   - ~~an `ObjectDoesNotExist` guard on `article.submission_data`, which the mapper's
     `getattr(article, "submission_data", None)` will not catch for a reverse one-to-one.~~
     **This claim was wrong, and nothing needed porting.** Checked directly against Django 4.2's own
     source (`db/models/fields/related_descriptors.py`):
     `ReverseOneToOneDescriptor.RelatedObjectDoesNotExist` explicitly inherits from `AttributeError`
     (`type("RelatedObjectDoesNotExist", (self.related.related_model.DoesNotExist, AttributeError),
     ...)`), so `getattr(article, "submission_data", None)` *does* correctly catch it.
     `mappers.py::_is_oa_agreed` already uses exactly this pattern and is correct as written.
3. ~~Settle open decision #2 (`decision_date` tie-break).~~ **Done** — resolved differently than either
   prototype: scoped to the accept decision at the highest review round, not by `EditorDecision`
   timestamp ordering (see the `<history>` section above).

## Prototype implementation

Lives in `wjs_review/metadata_export/` on `!1487` (`feature/metadata-export-dto-mappers`): DTO
(`dto.py`) + `formatters.py` (pure helpers) + `mappers.py` (free functions, `build_article_export_dto`
composes them) + `service.py` (the registry and both public entry points, below), rendered through
`templates/wjs_review/metadata_export/jcap_metadata_export.xml`. Tests updated to match every decision
in this document. `render_demo.py` renders real `dto.py` objects through the actual template
(standalone, DB-free proof, output under `metadata_export/example_output/`). Still a draft MR, not
meant to be merged as-is.

- **`export_jcap_xml`** management command (`management/commands/export_jcap_xml.py`) — prints the
  rendered XML for a given article id to stdout. Ported from the closed presenter branch, then
  updated to call `service.py`'s public entry point by its current name (see below).
- **`service.PUBLISHER_COMPOSERS`** — the `{journal_code: (compose_function, template_name)}` registry
  scaffolded per the portability rationale above (currently one entry, `"JCAP"`, plus the `None`
  default — both pointing at `build_article_export_dto`/`TEMPLATE_NAME`).
  `serialize_article_to_metadata_xml` is the **single** public entry point — registry-driven from the
  start, so there is no separate JCAP-specific wrapper function to keep in sync with it.
  `serialize_article_to_jcap_xml` (the earlier, JCAP-named wrapper) was removed as redundant once the
  registry made the generic name the only one anything needs to call.

## Open decisions

Every item raised in MR !1489's review, plus follow-up decisions made while incorporating that review
and while incorporating IOP's own feedback on a real sample export, is resolved as of this pass except
where *Still open* below says otherwise. Any future review comment reopens this section.

### Resolved

- **Architecture**: DTO+mappers (!1487); presenter prototype (!1488) dropped.
- **`article/@lang`**: `Article.language` is alpha-3; converted to alpha-2 for the export via
  `pycountry` (same lookup `wjs_tags.language_alpha2()` wraps).
- **`ms_no`**: always `article.articleworkflow.preprint_id`; the wjApp `preprintid` `Identifier` path
  is dropped entirely — imported articles are out of scope for this export, so no IOP notification is
  needed.
- **`article/@rev`**: a fixed literal (the XML schema/format version) from a per-journal setting,
  decoupled from `history/ms_id/rev_id`. Default `"2"`, no per-journal override defined yet.
- **`rev_id`**: a count of the article's major-revision `EditorDecision` rows; `0` if none.
  Re-settled during MR !1487 review — `revised_date` still uses the newest such row, independently.
- **`article_status`/`decision_status`**: this export only fires for accepted articles; any other
  stage raises rather than emitting empty XML (a precondition check, not covered by the "degrade
  gracefully" data policy below). No further `decision_status` vocabulary is needed.
- **`author/@user_id`**: always `frozen_author.pk`, never `frozen_author.author_id`/linked `Account` id.
- **File sources**: "Complete Document" group ← `article.manuscript_files`, not `article.galley_set`
  (no post-typesetting galleys exist yet at "accepted" stage); "Source Files" group ←
  `article.source_files` only, `article.supplementary_files` (ESM) dropped.
- **`revised_date`/`submitted_date`**: scoped to the article's latest major revision, not just the
  latest `RevisionRequest` of any kind; no fallback — both stay empty when the article has never had a
  major revision.
- **`decision_date`**: plain `article.date_accepted` (re-settled during MR !1487 review — this
  export only fires for accepted articles, so it's always set by then).
- **Abstract**: XML-escaped in addition to single-lined (HTML tags still not stripped, only escaped).
- **"OA licence type" vs. "Copyright/Licence Type"**: re-settled per IOP feedback —
  `"Copyright/Licence Type"` is the fixed literal `"Open Access"` when `OA Agreed="Yes"`, else the
  fixed literal `"Standard"` (IOP's own confirmed vocabulary, not `article.license.short_name`); both
  this field and `"OA licence type"` are omitted from the XML entirely (not just empty-valued) when
  `OA Agreed="No"`. See `<configurable_data_fields>` above.
- **Document envelope**: the rendered XML is wrapped in an `<!--sl.dtd-->` comment, `<!DOCTYPE
  article_set SYSTEM "sl.dtd">`, and an `<article_set dtd_version="4.28.5">`/`</article_set>` pair
  around the `<article>` element — IOP feedback; the original template was missing all three.
- **`author/@author_seq`**: the author's 1-indexed position in the ordered `frozenauthor_set`, not
  the raw `frozen_author.order` value (which can have gaps) — IOP feedback, requires consecutive
  numbering.
- **TA institution**: for the corresponding author of a transformative-agreement article only, the
  exported affiliation is `article.submission_data.affiliation` (when set) instead of the author's
  own `ControlledAffiliation` lookup — IOP feedback. Every other author, and every non-TA article, is
  unaffected; this is narrower than (and doesn't reopen) the earlier retracted proposal to use
  `submission_data.affiliation` as a blanket substitute.
- **Corresponding-author affiliation completeness**: IOP requires `inst`/`city`/`country` non-empty
  for the corresponding author. Since `ControlledAffiliation.organization` is nullable, this isn't
  guaranteed by the model — rather than fabricate data, a second EO message is logged (alongside the
  normal "export prepared" one) when the corresponding author's mapped affiliation comes out
  incomplete, flagging it for manual fixup. Does not block the export.
- **`total_pages_calc`**: computed once during mapping if not already set, persisted onto
  `Article.total_pages` (a deliberate write side-effect in the mapper, worth flagging in the eventual
  implementation).
- **OA-agreed access-mode allowlist**: default `["open-access"]`, no per-journal override defined
  yet. Same `{journal_code: [...], None: [...]}` shape as `OA_MESSAGE_CODES`, defined locally in
  `mappers.py` rather than reusing `OA_MESSAGE_CODES` itself.
- **Mapper error-raising policy**: mappers degrade gracefully, never raise, on missing/unexpected
  *data*. See the null-safety note in *Mapping specification* above.
- **Multiple affiliations per author**: by design, only the primary affiliation is ever exported
  (`affiliation[@seq="1"]`), not a full list even when a `FrozenAuthor` has several
  `ControlledAffiliation` rows. Confirmed as intentional, not a gap.
- **`file_designation` edge cases**: the application structure guarantees exactly one manuscript file
  and one source file per article in practice, even though the underlying `ManyToManyField` relations
  (`manuscript_files`, `source_files`) technically allow more. No special handling needed for a
  multiple-file case; the mapper can assume single-file groups.
- **Author-query duplication** (`frozenauthor_set` re-queried in both `map_authors` and
  `map_custom_fields`): **resolved by removal, not by caching** — `map_custom_fields` no longer
  queries `frozenauthor_set` at all now that the "Additional Authors" entry it built from that
  query is gone (re-settled, IOP feedback). `frozenauthor_set` is still queried separately by
  `corresponding_author_affiliation_is_incomplete`; left as-is, not ported from the presenter
  branch's `@cached_property` discipline — see *Practical next steps* above.
- **`ObjectDoesNotExist` guard on `article.submission_data`**: no change needed — the presenter
  branch's claim that `getattr(article, "submission_data", None)` doesn't catch this for a reverse
  one-to-one was checked against Django's own source and found incorrect; the mapper's existing
  `getattr` usage is already correct.
- **Per-publisher registry seam**: `service.PUBLISHER_COMPOSERS` (already scaffolded) **plus, as of
  this pass, the deeper per-field declarative rewrite too** — every `ArticleExportDTO` field gets its
  own single-value mapper, composed via a generic `compose_dto(article, dto_class, field_spec)` from a
  `JCAP_FIELDS` dict. Reverses the earlier "defer until a second publisher exists" call — see *The one
  real cost, and how to pay it* above for the full structure. Implemented in `!1487`'s code
  (`mappers.compose_dto`/`mappers.JCAP_FIELDS`).

### Still open

*(none — see Resolved below)*

### Resolved

- **`salutation` empty-tag fallback**: IOP feedback said an empty `<salutation>` fails ingestion, and
  `frozen_author.name_prefix` is often blank in practice. **Settled**: falls back to the fixed
  literal `"Dr."` when `name_prefix` is empty.
- **Addresses/countries missing across a whole real export batch**: IOP reported no `inst`/`city`/
  `country` for any author, including correspondents, across articles 5057/5061/5093/5095/5874/
  5955/5958/5965/5979/5987. Root-caused against real data via the `diagnose_export_affiliations`
  diagnostic — genuine upstream data gap, no mapper fix possible. See the *Investigated and closed*
  paragraph in the `<author_list><author>` section above for the full detail. Analysis suspended.

## Sample files

The two reference fixtures this document was originally built against
(`wjs/jcom_profile/tests/aux/JCAP__PeerReviewID_-metadata.xml`,
`.../JINSTJINST_010T_0526-metadata.xml`) were removed (MR !1487 review — coverage already lives in
`test_metadata_export.py::test_template_renders_from_hand_built_dto`), along with the `render_demo.py`
script and `example_output/*.xml` samples it generated. This document's field-by-field mapping above
remains the shape reference; there are no sample files committed alongside it any more.
