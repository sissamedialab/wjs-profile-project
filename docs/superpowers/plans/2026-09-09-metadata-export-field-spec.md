# Metadata Export Field-Spec Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `mappers.py`'s hand-written `build_article_export_dto` with a declarative
`{field_name: mapper_function}` field spec (`JCAP_FIELDS`) composed by a generic `compose_dto`
helper, so adding a second publisher later means defining a new field-spec dict, not subclassing or
branching this module.

**Architecture:** Every `ArticleExportDTO` field must have exactly one single-value mapper
(`article -> value`). The handful of current mappers that return more than one field's worth of
data get split; the handful of fields currently assembled inline in `build_article_export_dto` get
promoted to named mappers. `compose_dto(article, dto_class, field_spec)` then builds any DTO from
any such spec; `build_article_export_dto` becomes a one-line call into it with `ArticleExportDTO` +
`JCAP_FIELDS`. No individual mapper's *logic* changes — only how they're split, named, and wired
together. `service.py`'s `PUBLISHER_COMPOSERS` registry (already built) needs no changes.

**Tech Stack:** Python 3.11, Django 4.2, pytest + `pytest-django`, this repo's existing
`mappers.py`/`dto.py`/`formatters.py`/`service.py` split.

**Spec:** `docs/superpowers/specs/2026-09-02-iop-xml-export-design.md` (sections "The one real
cost, and how to pay it" and "Practical next steps" under "Architecture evaluation" specifically —
this plan implements that section verbatim).

## Global Constraints

- Line length 119 (`pyproject.toml`/`setup.cfg`), enforced by `black` + `flake8` via `pre-commit`
  (this repo uses black+isort+flake8, **not** ruff — see `.pre-commit-config.yaml`).
- `flake8-builtins`/`pep8-naming` are active: any field/attribute literally named `id` needs
  `# noqa: A003`; watch for this if a future field spec ever needs one (none of the fields touched
  here do).
- Mapper error policy (settled, spec's *Mapping specification*): mappers degrade gracefully, never
  raise, on missing/unexpected *data* — except `map_decision_status`/`map_decision_label`, which
  raise `UnsupportedArticleStageForExportError` for a non-accepted article (a precondition
  violation, not a data gap). This plan preserves that split exactly.
- No behavior change: every mapper's existing logic is preserved verbatim, only regrouped. No
  existing test's *assertion* should need to change — only its call site (which function it calls).
- Every task ends with `pytest ... -v` passing for the file touched, run from this repo against a
  Janeway environment with `plugins/wjs_review` symlinked to this worktree (already set up this
  session: `janeway/src/plugins/wjs_review -> wjs-profile-project-export-dto/wjs/plugins/wjs_review`,
  `DJANGO_SETTINGS_MODULE=core.settings`).

---

## Task 1: Split `map_decision_status_and_label` into `map_decision_status` + `map_decision_label`

**Files:**
- Modify: `wjs/plugins/wjs_review/metadata_export/mappers.py:125-138` (the function itself),
  `mappers.py:494` and `:504-505` (its call sites inside `build_article_export_dto`)
- Test: `wjs/plugins/wjs_review/tests/test_metadata_export.py:171-181`

**Interfaces:**
- Consumes: `STAGE_ACCEPTED` (already imported from `submission.models`),
  `UnsupportedArticleStageForExportError` (already defined in this module, `mappers.py:74`)
- Produces: `map_decision_status(article) -> str`, `map_decision_label(article) -> str`. Task 5's
  `JCAP_FIELDS` dict references both by name.

- [ ] **Step 1: Replace the old combined test with tests for the two new functions**

In `test_metadata_export.py`, replace:

```python
def test_map_decision_status_and_label_accepted(article):
    article.stage = "Accepted"
    assert mappers.map_decision_status_and_label(article) == ("accept", "Accepted")


@pytest.mark.parametrize("stage", ["Submitted", "Rejected", "Under Review"])
def test_map_decision_status_and_label_raises_for_unsupported_stage(article, stage):
    """Settled: this export only fires for accepted articles; any other stage raises."""
    article.stage = stage
    with pytest.raises(mappers.UnsupportedArticleStageForExportError):
        mappers.map_decision_status_and_label(article)
```

with:

```python
def test_map_decision_status_accepted(article):
    article.stage = "Accepted"
    assert mappers.map_decision_status(article) == "accept"


def test_map_decision_label_accepted(article):
    article.stage = "Accepted"
    assert mappers.map_decision_label(article) == "Accepted"


@pytest.mark.parametrize("stage", ["Submitted", "Rejected", "Under Review"])
def test_map_decision_status_raises_for_unsupported_stage(article, stage):
    """Settled: this export only fires for accepted articles; any other stage raises."""
    article.stage = stage
    with pytest.raises(mappers.UnsupportedArticleStageForExportError):
        mappers.map_decision_status(article)


@pytest.mark.parametrize("stage", ["Submitted", "Rejected", "Under Review"])
def test_map_decision_label_raises_for_unsupported_stage(article, stage):
    article.stage = stage
    with pytest.raises(mappers.UnsupportedArticleStageForExportError):
        mappers.map_decision_label(article)
```

- [ ] **Step 2: Run to verify it fails**

Run (from `janeway/src`): `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k decision_status_or_label -v`
Expected: FAIL — `AttributeError: module 'mappers' has no attribute 'map_decision_status'`

- [ ] **Step 3: Replace the combined function with the two split ones**

In `mappers.py`, replace:

```python
def map_decision_status_and_label(article) -> tuple[str, str]:
    """
    Map ``article.stage`` to ``(decision_status, decision_label)``.

    Settled (export_spec.md): only the "accept" case is supported. Any other stage raises
    ``UnsupportedArticleStageForExportError`` rather than emitting empty XML -- this export only ever
    fires for accepted articles, so reject/revise/pending and other stages are unreachable by
    design, not just unmapped.
    """
    if article.stage == STAGE_ACCEPTED:
        return "accept", "Accepted"
    raise UnsupportedArticleStageForExportError(
        f"metadata export only supports accepted articles; got stage={article.stage!r}",
    )
```

with:

```python
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
```

- [ ] **Step 4: Update `build_article_export_dto`'s call site**

In `mappers.py`'s `build_article_export_dto`, remove this line near the top of the function body:

```python
    decision_status, decision_label = map_decision_status_and_label(article)
```

and change the `ArticleExportDTO(...)` call's two matching kwargs from:

```python
        decision_status=decision_status,
        decision_label=decision_label,
```

to:

```python
        decision_status=map_decision_status(article),
        decision_label=map_decision_label(article),
```

- [ ] **Step 5: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "decision_status or decision_label or build_article_export_dto" -v`
Expected: PASS (all of `test_map_decision_status_*`, `test_map_decision_label_*`, and every existing
`test_build_article_export_dto_*` test, which are unaffected by this split).

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "refactor(metadata-export): split map_decision_status_and_label into two single-field mappers"
```

---

## Task 2: Split `map_history_dates` into four single-field mappers

**Files:**
- Modify: `mappers.py:311-335` (the function itself; the three private helpers it calls —
  `_latest_major_revision_editor_decision` at `:258-263`, `_latest_major_revision_request` at
  `:280-286`, `_resolve_decision_date` at `:289-308` — are unchanged, each new mapper just calls the
  one(s) it needs), `mappers.py:495` and `:514-517` (call sites)
- Test: `test_metadata_export.py:336-406` (the eight `test_map_history_dates_*` tests)

**Interfaces:**
- Consumes: `build_date_parts` (from `.formatters`, already imported), `DateParts` (from `.dto`,
  already imported), the three private helpers named above (unchanged signatures: all take
  `article`, return an `EditorDecision`/`RevisionRequest`/datetime or `None`)
- Produces: `map_received_date(article) -> DateParts`, `map_revised_date(article) -> DateParts`,
  `map_submitted_date(article) -> DateParts`, `map_decision_date(article) -> DateParts`. Task 5's
  `JCAP_FIELDS` references all four by name.

- [ ] **Step 1: Replace the eight combined tests with per-function tests**

In `test_metadata_export.py`, replace the entire block from `test_map_history_dates_first_submission_has_no_dates`
through `test_map_history_dates_decision_date_uses_accept_decision` (lines 336-406) with:

```python
def test_map_received_date_uses_date_submitted(assigned_article):
    assert mappers.map_received_date(assigned_article) == formatters.build_date_parts(
        assigned_article.date_submitted,
    )


def test_map_received_date_empty_when_never_submitted(article):
    assert not mappers.map_received_date(article), "the article fixture has no date_submitted"


def test_map_revised_date_empty_with_no_major_revision(assigned_article):
    assert not mappers.map_revised_date(assigned_article)


def test_map_revised_date_uses_latest_major_revision_decision(assigned_article, editor_revision):
    major_revision_decision = (
        assigned_article.articleworkflow.decisions.filter(decision=ArticleWorkflow.Decisions.MAJOR_REVISION)
        .order_by("-created")
        .first()
    )
    assert major_revision_decision is not None, "editor_revision must have created a major-revision EditorDecision"

    expected = formatters.build_date_parts(major_revision_decision.modified or major_revision_decision.created)
    assert mappers.map_revised_date(assigned_article) == expected


def test_map_submitted_date_empty_with_no_major_revision(assigned_article):
    assert not mappers.map_submitted_date(assigned_article)


def test_map_submitted_date_empty_while_revision_in_progress(assigned_article, editor_revision):
    assert editor_revision.date_completed is None, "sanity check: the revision hasn't been submitted yet"

    assert not mappers.map_submitted_date(
        assigned_article,
    ), "settled: no fallback -- submitted_date stays empty until the revision is completed"


def test_map_submitted_date_uses_completed_major_revision_request(assigned_article, editor_revision):
    editor_revision.date_completed = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    editor_revision.save()

    assert mappers.map_submitted_date(assigned_article) == formatters.build_date_parts(editor_revision.date_completed)


def test_map_decision_date_falls_back_without_editor_decision(assigned_article):
    assigned_article.date_accepted = None
    assigned_article.date_declined = None

    assert not mappers.map_decision_date(
        assigned_article,
    ), "no EditorDecision and no date_accepted/declined must leave decision_date empty"


def test_map_decision_date_uses_accept_decision(accepted_article):
    accept_decision = (
        accepted_article.articleworkflow.decisions.filter(decision=ArticleWorkflow.Decisions.ACCEPT)
        .order_by("-review_round__round_number")
        .first()
    )
    assert accept_decision is not None, "accepted_article must have gone through HandleDecision"

    expected = formatters.build_date_parts(accept_decision.modified or accept_decision.created)
    assert mappers.map_decision_date(accepted_article) == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "received_date or revised_date or submitted_date or decision_date" -v`
Expected: FAIL — `AttributeError: module 'mappers' has no attribute 'map_received_date'`

- [ ] **Step 3: Replace `map_history_dates` with four single-field mappers**

In `mappers.py`, replace:

```python
def map_history_dates(article) -> tuple[DateParts, DateParts, DateParts, DateParts]:
    """
    Map the four ``<history><ms_id>`` dates: ``(received, revised, submitted, decision)``.

    Settled (export_spec.md): ``revised_date``/``submitted_date`` are scoped to the article's
    latest **major** revision, not just the latest ``RevisionRequest`` of any kind -- and there is
    no fallback any more: both stay empty whenever the article has never had a major revision
    (straight accept, or only minor/technical revisions along the way).
    """
    major_revision_decision = _latest_major_revision_editor_decision(article)
    revised_date = (
        build_date_parts(major_revision_decision.modified or major_revision_decision.created)
        if major_revision_decision is not None
        else DateParts()
    )

    major_revision_request = _latest_major_revision_request(article)
    submitted_date = (
        build_date_parts(major_revision_request.date_completed) if major_revision_request is not None else DateParts()
    )

    received_date = build_date_parts(article.date_submitted)
    decision_date = build_date_parts(_resolve_decision_date(article))

    return received_date, revised_date, submitted_date, decision_date
```

with:

```python
def map_received_date(article) -> DateParts:
    """Map ``history/received_date``: the article's original, first-ever submission date."""
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
    """Map ``history/decision_date``. See ``_resolve_decision_date`` for the settled sourcing rule."""
    return build_date_parts(_resolve_decision_date(article))
```

- [ ] **Step 4: Update `build_article_export_dto`'s call site**

Remove this line from `build_article_export_dto`:

```python
    received_date, revised_date, submitted_date, decision_date = map_history_dates(article)
```

and change these four kwargs in the `ArticleExportDTO(...)` call from:

```python
        received_date=received_date,
        revised_date=revised_date,
        submitted_date=submitted_date,
        decision_date=decision_date,
```

to:

```python
        received_date=map_received_date(article),
        revised_date=map_revised_date(article),
        submitted_date=map_submitted_date(article),
        decision_date=map_decision_date(article),
```

- [ ] **Step 5: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "received_date or revised_date or submitted_date or decision_date or build_article_export_dto" -v`
Expected: PASS — all 8 new tests, plus every existing `test_build_article_export_dto_*` test still
passing unchanged.

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "refactor(metadata-export): split map_history_dates into four single-field mappers"
```

---

## Task 3: Split `map_funders` into `map_funders_list` + `map_no_funders`

**Files:**
- Modify: `mappers.py:470-484` (the function), `mappers.py:496` and `:525-526` (call sites)
- Test: `test_metadata_export.py:597-615`

**Interfaces:**
- Consumes: `FunderExportDTO` (from `.dto`, already imported)
- Produces: `map_funders_list(article) -> list[FunderExportDTO]`, `map_no_funders(article) -> bool`.
  Task 5's `JCAP_FIELDS` references both by name (as `"funders"`/`"no_funders"`).

- [ ] **Step 1: Replace the combined tests with per-function tests**

Replace:

```python
def test_map_funders_with_no_funding_rows_returns_one_placeholder(article):
    funders, no_funders = mappers.map_funders(article)

    assert no_funders is True
    assert funders == [FunderExportDTO()]


def test_map_funders_with_funding_rows(article):
    ArticleFunding.objects.create(
        article=article,
        name="Some Funder",
        fundref_id="https://doi.org/10.1/x",
        funding_id="G-123",
    )

    funders, no_funders = mappers.map_funders(article)

    assert no_funders is False
    assert funders == [FunderExportDTO(name="Some Funder", fundref_id="https://doi.org/10.1/x", funding_id="G-123")]
```

with:

```python
def test_map_funders_list_with_no_funding_rows_returns_one_placeholder(article):
    assert mappers.map_funders_list(article) == [FunderExportDTO()]


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


def test_map_no_funders_true_when_no_funding_rows(article):
    assert mappers.map_no_funders(article) is True


def test_map_no_funders_false_with_funding_rows(article):
    ArticleFunding.objects.create(article=article, name="Some Funder")
    assert mappers.map_no_funders(article) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "funders" -v`
Expected: FAIL — `AttributeError: module 'mappers' has no attribute 'map_funders_list'`

- [ ] **Step 3: Replace `map_funders` with the two split functions**

Replace:

```python
def map_funders(article) -> tuple[list[FunderExportDTO], bool]:
    """
    Map ``article.articlefunding_set`` to ``(funders, no_funders)``.

    When there are no ``ArticleFunding`` rows, still returns exactly one empty placeholder
    ``FunderExportDTO``, matching the shape both reference fixtures use when ``no_funders=True``.
    """
    funding_rows = list(article.articlefunding_set.all())
    if not funding_rows:
        return [FunderExportDTO()], True

    return [
        FunderExportDTO(name=funding.name, fundref_id=funding.fundref_id or "", funding_id=funding.funding_id or "")
        for funding in funding_rows
    ], False
```

with:

```python
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
```

- [ ] **Step 4: Update `build_article_export_dto`'s call site**

Remove this line from `build_article_export_dto`:

```python
    funders, no_funders = map_funders(article)
```

and change these two kwargs in the `ArticleExportDTO(...)` call from:

```python
        funders=funders,
        no_funders=no_funders,
```

to:

```python
        funders=map_funders_list(article),
        no_funders=map_no_funders(article),
```

- [ ] **Step 5: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "funders or build_article_export_dto" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "refactor(metadata-export): split map_funders into map_funders_list + map_no_funders"
```

---

## Task 4: Promote `title`/`subtitle`/`publication_type`/`abstract` to named mappers

**Files:**
- Modify: `mappers.py:487-527` (`build_article_export_dto`'s inline expressions for these 4 kwargs)
- Test: `test_metadata_export.py` (new tests added near the other `map_*` tests; the existing
  `test_build_article_export_dto_abstract_has_no_line_breaks`,
  `test_build_article_export_dto_subtitle_none_is_empty_string`,
  `test_build_article_export_dto_publication_type_section_name_none_is_empty_string` at lines
  635-663 stay exactly as they are, as integration-level smoke tests)

**Interfaces:**
- Consumes: `to_single_line` (from `.formatters`, already imported)
- Produces: `map_title(article) -> str`, `map_subtitle(article) -> str`,
  `map_publication_type(article) -> str`, `map_abstract(article) -> str`. Task 5's `JCAP_FIELDS`
  references all four by name.

- [ ] **Step 1: Add new focused unit tests for the four mappers**

Add these near the other single-field mapper tests in `test_metadata_export.py` (e.g. after
`test_map_pii`):

```python
def test_map_title(article):
    assert mappers.map_title(article) == article.title


def test_map_subtitle_none_is_empty_string(article):
    article.subtitle = None
    assert mappers.map_subtitle(article) == ""


def test_map_publication_type_section_name_none_is_empty_string(article):
    article.section = Section.objects.create(journal=article.journal, name=None)
    assert mappers.map_publication_type(article) == ""


def test_map_publication_type_no_section_is_empty_string(article):
    article.section = None
    assert mappers.map_publication_type(article) == ""


def test_map_abstract_collapses_line_breaks(article):
    article.abstract = "First paragraph.\n\nSecond paragraph,\r\nwrapped across two lines."
    assert mappers.map_abstract(article) == "First paragraph. Second paragraph, wrapped across two lines."
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_title or map_subtitle or map_publication_type or map_abstract" -v`
Expected: FAIL — `AttributeError: module 'mappers' has no attribute 'map_title'`

- [ ] **Step 3: Add the four mapper functions**

Add these to `mappers.py`, just before `build_article_export_dto`:

```python
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
```

- [ ] **Step 4: Update `build_article_export_dto`'s call site**

Change these kwargs in the `ArticleExportDTO(...)` call from:

```python
        title=article.title or "",
        subtitle=article.subtitle or "",
        # See `map_custom_fields`'s "Section" entry: `Section.name` is nullable even when a
        # `Section` is set.
        publication_type=(article.section.name or "") if article.section else "",
```

to:

```python
        title=map_title(article),
        subtitle=map_subtitle(article),
        publication_type=map_publication_type(article),
```

and:

```python
        # `to_single_line` handles whitespace normalization only; XML-escaping (settled,
        # export_spec.md) is handled by the template's default Django autoescaping (`{{ }}`),
        # not here -- see the template-contract test asserting `<`/`&` come out escaped.
        abstract=to_single_line(article.abstract or ""),
```

to:

```python
        abstract=map_abstract(article),
```

- [ ] **Step 5: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "map_title or map_subtitle or map_publication_type or map_abstract or build_article_export_dto" -v`
Expected: PASS — the 5 new tests, plus the 3 existing `build_article_export_dto` tests covering
these same fields, all unchanged.

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "refactor(metadata-export): promote title/subtitle/publication_type/abstract to named mappers"
```

---

## Task 5: Add `compose_dto` + `JCAP_FIELDS`, rewrite `build_article_export_dto`

**Files:**
- Modify: `mappers.py:487-527` (replace the whole `build_article_export_dto` body)
- Test: `test_metadata_export.py` (new tests; needs a new `import dataclasses` at the top)

**Interfaces:**
- Consumes: every `map_*` function now defined in `mappers.py` (23 of them, one per
  `ArticleExportDTO` field — see the full list in Step 3 below), `ArticleExportDTO` (from `.dto`,
  already imported)
- Produces: `compose_dto(article, dto_class, field_spec) -> Any`, `JCAP_FIELDS: dict[str, Callable]`
  (the seam a second publisher extends — not part of this plan, but documented in the spec).
  `build_article_export_dto(article) -> ArticleExportDTO` keeps its existing name and signature, so
  no other module (`service.py`, the management command, or any test outside this file) needs to
  change.

- [ ] **Step 1: Add `import dataclasses` to the test file**

At the top of `test_metadata_export.py`, add `import dataclasses` alongside the existing `import
datetime` (both are stdlib, so they sort together via isort).

- [ ] **Step 2: Add the field-spec coverage and composition tests**

Add these near the end of the "mappers.py" test section, just before the `build_article_export_dto`
tests:

```python
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
```

- [ ] **Step 3: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -k "jcap_fields or compose_dto" -v`
Expected: FAIL — `AttributeError: module 'mappers' has no attribute 'JCAP_FIELDS'`

- [ ] **Step 4: Replace `build_article_export_dto` with the field-spec-driven version**

Replace the entire function (from `def build_article_export_dto(article) -> ArticleExportDTO:`
through its closing `)`) with:

```python
def compose_dto(article, dto_class, field_spec):
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
```

- [ ] **Step 5: Run to verify it passes**

Run the full file: `DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest
../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -v`
Expected: PASS — every test in the file, including all `build_article_export_dto`/`service.py`/
template-contract tests, which exercise `build_article_export_dto` end-to-end and must show no
behavior change.

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/metadata_export/mappers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "refactor(metadata-export): compose build_article_export_dto from a declarative JCAP_FIELDS spec"
```

---

## Task 6: End-to-end verification, design doc update, push

**Files:**
- Modify (docs repo, sibling worktree `wjs-profile-project-iop-xml-design`,
  branch `feature/issue-generate-iop-xml`):
  `docs/superpowers/specs/2026-09-02-iop-xml-export-design.md` (the "Prototype implementation"
  section and the "Per-publisher registry seam" bullet under "Open decisions" → "Resolved", both
  currently say this is "not yet implemented" / "the plan an implementation pass will follow" —
  update to say it's implemented)
- No other files.

- [ ] **Step 1: Run the whole test module one more time from a clean checkout of the changes**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/janeway/src
DJANGO_SETTINGS_MODULE=core.settings python3 -m pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py -v
```

Expected: PASS, full file, no skips.

- [ ] **Step 2: Confirm the management command still works against real data**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/janeway/src
DJANGO_SETTINGS_MODULE=core.settings python3 manage.py export_jcap_xml 5874
```

Expected: the same well-formed XML this command has produced all along for this article (compare
`ms_no="JCAP_5874"`, `rev="2"`, non-empty `<history>` dates) — proving `compose_dto`/`JCAP_FIELDS`
produce byte-for-byte the same output as the hand-written version did.

- [ ] **Step 3: Update the design doc's "Prototype implementation" section**

In the docs worktree, find the bullet:

```
- **Per-publisher registry seam**: `service.PUBLISHER_COMPOSERS` (already scaffolded) **plus, as of
  this pass, the deeper per-field declarative rewrite too** — every `ArticleExportDTO` field gets its
  own single-value mapper, composed via a generic `compose_dto(article, dto_class, field_spec)` from a
  `JCAP_FIELDS` dict. Reverses the earlier "defer until a second publisher exists" call — see *The one
  real cost, and how to pay it* above for the full structure. Not yet implemented in `!1487`'s code;
  this section is the plan an implementation pass will follow.
```

and replace the last sentence:

```
Not yet implemented in `!1487`'s code; this section is the plan an implementation pass will follow.
```

with:

```
Implemented in `!1487`'s code (`mappers.compose_dto`/`mappers.JCAP_FIELDS`).
```

- [ ] **Step 4: Commit and push both repos**

```bash
# code repo
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-export-dto
git push origin feature/metadata-export-dto-mappers

# docs repo
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project-iop-xml-design
git add docs/superpowers/specs/2026-09-02-iop-xml-export-design.md
git commit -m "docs(export): mark the field-spec refactor as implemented"
git push origin feature/issue-generate-iop-xml
```
