# StaffKeyword unique (parameters, keyword) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The database refuses a second `StaffKeyword` for the same `(parameters, keyword)`, and the wjapp importers merge duplicate source keywords instead of failing.

**Architecture:** A `UniqueConstraint` on `StaffKeyword` with a constraint-only migration (no data step). A pure helper `merge_editor_keywords` in `import_logic.py` collapses the wjapp rows of an editor into one weight per normalized keyword (highest wins); both importers use it.

**Tech Stack:** Django 5.2 (Janeway), pytest-django, run from `janeway/src`.

**Spec:** `artifacts/specs/2026-11-18-staffkeyword-unique-design.md` (issue wjs/specs#3436)

## Global Constraints

- Black line length 119, double quotes, docstrings per `.claude/rules/code-style-python.md`; no local imports.
- Model `Meta` has `verbose_name`, `verbose_name_plural`, `ordering = ["parameters", "keyword"]` and the constraint
  named `unique_staffkeyword_parameters_keyword`.
- Migration is `jcom_profile/0046_...`, depends on `0045_clear_salutation`, has NO `RunPython` step (it must fail
  loudly if a database still has duplicates). Never commit a migration generated inside the docker env.
- Duplicate source keywords: keep the highest weight, log a warning, never raise.
- Run tests from `janeway/src`: `pytest ../../wjs-profile-project/<path>`.
- Commit cadence is decided at flow step 4: tasks end with a checkpoint, not a commit.

## Review Focus

- Same keyword twice with equal weights: one row, no error.
- JCOMAL `"a;b;c"` / `"a;x;y"` strings normalize to the same word: merged, not two rows.
- Weight 0 next to a positive weight for the same keyword: the positive one wins.
- `sync_editor` (not `clear`) with a row already in the database: its weight is left alone, no `IntegrityError`.
- Editor with no wjapp keywords: nothing is created, nothing breaks.

---

### Task 1: Unique constraint on StaffKeyword

**Files:**
- Modify: `wjs/jcom_profile/models.py` (class `StaffKeyword`, ~line 317)
- Create: `wjs/jcom_profile/migrations/0046_<generated>.py`
- Test: `wjs/jcom_profile/tests/test_staffkeyword.py` (create)

**Interfaces:**
- Produces: DB-level uniqueness on `(parameters, keyword)`; later tasks rely on `IntegrityError` being impossible
  for merged keyword sets.

- [ ] **Step 1: Write the failing tests** in `test_staffkeyword.py` (`@pytest.mark.django_db`; fixtures `section_editor`,
  `editor`, `journal`, `keywords` from `jcom_profile/tests/conftest.py`):
  - `test_duplicate_staff_keyword_is_rejected`: create `StaffWorkloadParameters` for `section_editor`/`journal`, a
    `StaffKeyword` for `keywords[0]`; creating a second one for the same pair raises `IntegrityError` inside
    `transaction.atomic()`.
  - `test_same_keyword_for_different_parameters_is_allowed`: same keyword on the parameters of two different editors
    creates two rows.
  - `test_different_keywords_for_same_parameters_are_allowed`: two keywords, same parameters, two rows.
  - Every assertion has a message (`assert ..., "<why>"`).

- [ ] **Step 2: Run** `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_staffkeyword.py -v` from `janeway/src`.
  Expected: `test_duplicate_staff_keyword_is_rejected` FAILS (no `IntegrityError` raised), the other two pass.

- [ ] **Step 3: Add `class Meta`** to `StaffKeyword` (after the fields, before `__str__`, per
  `code-style-django.md`) with `verbose_name=_("Staff keyword")`, `verbose_name_plural=_("Staff keywords")`,
  `ordering=["parameters", "keyword"]`, `constraints=[models.UniqueConstraint(fields=["parameters", "keyword"],
  name="unique_staffkeyword_parameters_keyword")]`. Then from `janeway/src` run
  `python manage.py makemigrations jcom_profile -n staffkeyword_unique_parameters_keyword` and check the file is
  `0046_...`, contains only `AlterModelOptions` + `AddConstraint`, and depends on `0045_clear_salutation`.

- [ ] **Step 4: Run** the new test file, then `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_deactivated_keywords.py
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`, and
  `python manage.py makemigrations --check --dry-run`. Expected: all PASS, "No changes detected".

- [ ] **Step 5: Checkpoint** (no commit yet): `git status` shows the model, the migration and the new test only.

### Task 2: Importers merge duplicate keywords

**Files:**
- Modify: `wjs/jcom_profile/import_wjapp/import_logic.py` (add helper after `normalize_keyword`, ~line 474; use it in
  `EditorAssignmentAction.set_editor_parameters`, ~line 1546)
- Modify: `wjs/jcom_profile/import_wjapp/import_editor_keywords.py` (`sync_editor`, ~lines 140-175)
- Test: `wjs/jcom_profile/tests/test_import_editor_keywords.py` (create)

**Interfaces:**
- Consumes: Task 1's constraint (a duplicate `create` would now raise).
- Produces: `merge_editor_keywords(journal_code: str, editor_keywords: Iterable[dict]) -> dict[str, int]` in
  `import_logic.py`: maps the normalized word (`normalize_keyword(journal_code, row["keywordName"].strip())`) to the
  highest `row["keywordWeight"]` among the rows with that word; logs one warning per merged word via the module
  `logger`; preserves first-seen order.

- [ ] **Step 1: Write the failing helper tests** (plain functions, no DB; patch the logger with
  `mock.patch.object(import_logic.logger, "warning")` to assert the warning):
  - `test_merge_keeps_highest_weight`: rows `("stars", 3)`, `("stars", 7)` for `"JCOM"` -> `{"stars": 7}`, one warning.
  - `test_merge_equal_weights_gives_one_entry`: `("stars", 5)` twice -> `{"stars": 5}`.
  - `test_merge_zero_and_positive_weight`: `("stars", 0)`, `("stars", 2)` -> `{"stars": 2}`.
  - `test_merge_jcomal_variants_collapse`: `"Stars;Estrellas;Stelle"` and `"Stars;X;Y"` for `"JCOMAL"` -> one `"Stars"` entry.
  - `test_merge_strips_and_keeps_order`: `" b "`, `"a"`, `"b"` -> keys in order `["b", "a"]`.
  - `test_merge_no_duplicates_no_warning`: distinct words -> no warning call.

- [ ] **Step 2: Write the failing `sync_editor` tests** (`@pytest.mark.django_db`; fixtures `section_editor`, `journal`;
  `monkeypatch` `import_editor_keywords.account_get_or_create_check_correspondence` to return `section_editor` and
  `import_editor_keywords.read_editor_keywords` to return rows; pass `connection=None` and a `row` dict with
  `userCod`, `lastname`, `firstname`, `email`, `privacy`, `editorWorkload`):
  - `test_sync_editor_merges_duplicate_source_keywords`: rows `("stars", 3)`, `("stars", 7)` -> exactly one
    `StaffKeyword`, weight 7, `result.keywords_added == 1`, no exception.
  - `test_sync_editor_does_not_overwrite_existing_weight`: pre-create the `StaffKeyword` with weight 9, sync rows
    `("stars", 3)` without `clear` -> still one row, weight 9, `keywords_added == 0`.
  - `test_sync_editor_clear_replaces_keywords`: pre-create `StaffKeyword` for another keyword, sync with `clear=True`
    -> only the synced keyword remains.
  - `test_sync_editor_without_keywords_creates_nothing`: `read_editor_keywords` returns `[]` -> no `StaffKeyword`,
    no `StaffWorkloadParameters`.

- [ ] **Step 3: Run** `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_import_editor_keywords.py -v`.
  Expected: helper tests FAIL (`merge_editor_keywords` missing); `sync_editor` duplicate test FAILS
  (`IntegrityError` or two rows).

- [ ] **Step 4: Implement `merge_editor_keywords`** in `import_logic.py` (algorithm: iterate rows, key = normalized
  stripped word, keep `max` weight, warn once per word that appears again, return a plain `dict`).

- [ ] **Step 5: Use it in `sync_editor`**: build `merged = merge_editor_keywords(journal.code, editor_keywords)`; after the
  optional `clear` delete, for each `(word, weight)` do `Keyword.objects.get_or_create(word=word)` (keep the existing
  "Created keyword ... Please check!" warning), then
  `StaffKeyword.objects.get_or_create(parameters=..., keyword=..., defaults={"weight": weight})`; increment
  `keywords_added` only when created. Remove the "duplicate rows ... left as-is" branch and its comment. The final
  debug log uses `len(merged)`.

- [ ] **Step 6: Use it in `set_editor_parameters`**: same loop over `merge_editor_keywords(self.journal.code,
  editor_parameters)`, with `StaffKeyword.objects.create(...)` kept (rows were deleted just before).

- [ ] **Step 7: Run** the new test file, then `pytest ../../wjs-profile-project/wjs/jcom_profile/tests -k "keyword or import" -q`.
  Expected: all PASS.

- [ ] **Step 8: Checkpoint**, then run `pre-commit run --all-files` from the repo root. Expected: clean.
