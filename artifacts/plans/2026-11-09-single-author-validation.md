# Correspondence-author required-fields dict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate correspondence-author rules from one overridable per-journal dict (fields + public labels) via a shared helper, keep the `*_validation` functions as injection points, remove the `*_missing_fields` functions, and prove exact equivalence with the old behavior.

**Architecture:** S (`wjs-submission-project`) gains the dict setting, the entry evaluator `missing_required_fields(user, journal)` and the new `validation(user, journal)` contract. P (`wjs-profile-project`) supplies the JCOM/JCOMAL/JCAP entries and keeps thin jcom/jcap validation functions. Equivalence tests with frozen old implementations are written first in each task (RED) and drive the code.

**Tech Stack:** Python 3, Django 5.2, pytest / pytest-django.

**Spec:** `wjs-profile-project/artifacts/specs/2026-11-09-single-author-validation-design.md`

## Global Constraints

- Docstrings, comments, commits in English; commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; conventional commits; per the chosen commit strategy; nothing pushed.
- S must not import `wjs.jcom_profile`. Labels in settings use `gettext_lazy`.
- Entry format: `{"label": str, "paths": list[str], "allowed": optional collection}`; no `allowed` -> satisfied if ANY path value is truthy; with `allowed` -> satisfied if ANY path value `in allowed` (no truthiness test). Callables in a path are called. Unsatisfied entries yield their label in dict order. Settings: `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` -> S `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`, journal code keys with `None` fallback; overrides repeat `None`.
- Validation contract: `validation(user: Account, journal: Journal) -> bool`.
- Labels unchanged: "Active account", "Name", "First name", "Email", "Affiliation", "Profession", "Biography", `PROFESSIONAL_LINKS_LABEL` ("At least one of: SciX, INSPIRE, arXiv or other records, Facebook, Twitter, LinkedIn").
- Rules (must match old behavior): default = active, (last or first name), email, `institution`. JCOM/JCOMAL = active, (last or first name), email, profession in PROFESSIONS keys, biography, any of `jcomprofile.records_scix/inspire/arxiv/other`, `facebook`, `twitter`, `linkedin`. JCAP = active, `first_name`, email, `affiliation()` (callable), same links group.
- Run pytest from `janeway/src`: `pytest --create-db ../../<repo>/<path> -k <name>`; run pre-commit in each repo before committing. Old references live only in tests.

## Review Focus

1. Profession `None`, `""`, a valid choice, and an out-of-choices value (the last must be missing and listed).
2. JCAP: `first_name` set, `last_name` empty is valid; `last_name` only is invalid. JCOM/default: either name suffices.
3. Whitelisted user with several missing fields is eligible; same user not whitelisted is not.
4. Journal code absent from the dict falls back to the `None` entry; JCOM and JCOMAL read their own entries.
5. `missing_required_fields` returns a `list` in dict order; an empty list means valid.

---

### Task 1: S — dict, helper, default validation, eligibility, step 4

**Files:**
- Modify: `wjs-submission-project/wjs/plugins/wjs_submission/account_validation.py`, `.../settings.py`, `.../models.py` (docstring ref ~line 595), `tests/test_account_validation.py`, `tests/test_views.py` (~line 857); docs/CHANGELOG mentioning removed names.

**Interfaces:**
- Produces: `missing_required_fields(user: Account, journal: Journal) -> list[str]`; `default_correspondence_author_validation(user: Account, journal: Journal) -> bool`; `get_correspondence_author_missing_fields(journal, user) -> list[str]` (same signature as today); `is_user_eligible_for_correspondence_author(journal, user) -> bool` (same signature); settings `DEFAULT_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`, `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`.

- [ ] **Step 1: Write failing tests** in `tests/test_account_validation.py`: (a) `test_default_validation_equivalent_to_old`: copy verbatim the old validator from `git show 23be0c8^:wjs/plugins/wjs_submission/account_validation.py` and the current `default_correspondence_author_missing_fields` (from HEAD) as `_old_validation` / `_old_missing_fields`; stub users (`SimpleNamespace`: `is_active, first_name, last_name, email, institution`), stub journal `SimpleNamespace(code="UNKNOWN")`; sweep the cartesian product and assert both equivalences from the spec. (b) evaluator tests with a patched `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`: any-of truthy, `allowed` membership (`"0"` allowed, `""` and out-of-set not), callable path, label order, unknown code falls to `None`. (c) eligibility: valid user eligible; missing institution not; whitelisted eligible; injected validation function (patch `CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION` to a stub dotted path returning `True`) overrides the dict and receives `(user, journal)`. Run: all FAIL/ImportError.
- [ ] **Step 2: Implement** in `settings.py`: `DEFAULT_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS = {None: [...]}` (the default rules, labels via `gettext_lazy`) and `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS = getattr(settings, "SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS", DEFAULT...)`; delete both `*_MISSING_FIELDS_FUNCTION` names; keep `DEFAULT_/CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION`, reword their comment (journals inject custom logic here; default reads the dict; function signature `(user, journal)`). In `account_validation.py`: add `missing_required_fields` (path traversal helper private), make `default_correspondence_author_validation(user, journal)` call it, delete `default_correspondence_author_missing_fields`, update `is_user_eligible_for_correspondence_author` to call `control_function(user, journal)` after the whitelist check, and make `get_correspondence_author_missing_fields` return `[]` for whitelisted users else `missing_required_fields(user, journal)`.
- [ ] **Step 3: Port existing tests**: replace the five direct `default_correspondence_author_validation(user)` tests and the `default_..._missing_fields` tests with `(user, journal)` / `missing_required_fields` equivalents; update `test_get_correspondence_author_missing_fields_*` and the patch in `tests/test_views.py` to patch `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` instead of the removed setting. Fix the `models.py` docstring path.
- [ ] **Step 4: Run** `pytest --create-db ../../wjs-submission-project/tests/test_account_validation.py ../../wjs-submission-project/tests/test_views.py`: PASS. Mutation check: drop the institution entry from the default, see the equivalence test FAIL, revert.
- [ ] **Step 5: Commit** (after approval) `refactor: evaluate correspondence author rules from a per-journal required-fields dict`.

### Task 2: P — journal entries, thin jcom/jcap validation, remove missing_fields

**Files:**
- Modify: `wjs-profile-project/wjs/jcom_profile/account_validation.py`, `wjs/defaults/settings.py` (~lines 747-756, 791-797), `wjs/jcom_profile/tests/test_account_validation.py`; docs/CHANGELOG mentioning removed names.

**Interfaces:**
- Consumes: Task 1 settings and `(user, journal)` contract.
- Produces: `jcom_correspondence_author_validation(user: Account, journal: Journal) -> bool` and `jcap_correspondence_author_validation(user, journal) -> bool` (each `not missing_required_fields(user, journal)`); setting `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` with keys `JCOM`, `JCOMAL`, `JCAP`, `None`; `PROFESSIONAL_LINKS_LABEL` kept (used by the dict).

- [ ] **Step 1: Write failing tests** in `wjs/jcom_profile/tests/test_account_validation.py`: baseline first — run the file and note which tests fail on stale lowercase labels (they are replaced in this task). Write `test_jcom_validation_equivalent_to_old` (also run for journal code `JCOMAL`) and `test_jcap_validation_equivalent_to_old`: verbatim copies of the old validators (`git show f84bae5c:wjs/jcom_profile/account_validation.py`) and of the current `jcom_/jcap_correspondence_author_missing_fields` (HEAD) as references; stub users per the spec sweep (`jcomprofile` SimpleNamespace with `profession` and the four `records_*`, plus `facebook/twitter/linkedin`, callable `affiliation()`); assert both equivalences, plus out-of-choices profession is invalid and listed as "Profession". Add `test_required_fields_setting_shape`: keys `{"JCOM","JCOMAL","JCAP",None}`, every entry has `label`/`paths`, `None` entry equals S default. Run: FAIL.
- [ ] **Step 2: Implement.** In `wjs/defaults/settings.py` replace both removed settings with `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` (JCOM and JCOMAL share the same entries list; JCAP its own; `None` the default entries repeated) and keep `SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION` pointing at the thin jcom/jcap functions. In `account_validation.py` delete the two `*_missing_fields` functions and the `_has_professional_links` helper, make `jcom_/jcap_correspondence_author_validation` one-line wrappers over S's `missing_required_fields`, and update the module docstring.
- [ ] **Step 3: Port tests**: replace the direct validation tests and the `*_missing_fields` tests with assertions through the new functions, reusing the `_make_*_valid` helpers, covering: valid, inactive, missing biography/profession/out-of-choices, names (JCOM either; JCAP `first_name` only), email, links none / social handle alone, JCAP affiliation, JCAP ignores profession/biography; update the setting-resolution tests to the new setting and the validation setting.
- [ ] **Step 4: Run** `pytest --create-db ../../wjs-profile-project/wjs/jcom_profile/tests/test_account_validation.py`: PASS. Mutation check on the JCOM profession entry. Grep both repos (excluding `.claude/`, `node_modules`) for `_missing_fields_function`, `MISSING_FIELDS_FUNCTION`, `jcom_correspondence_author_missing_fields`: only references inside test reference copies remain.
- [ ] **Step 5: Commit** (after approval) `refactor: supply JCOM/JCAP required fields via setting, drop missing_fields functions`.

### Task 3: Cross-repo check and changelog

- [ ] **Step 1:** From `janeway/src` run: `pytest --create-db ../../wjs-submission-project/tests/test_account_validation.py ../../wjs-submission-project/tests/test_views.py ../../wjs-profile-project/wjs/jcom_profile/tests/test_account_validation.py`, plus any P test importing step 4 (`grep -rl "wjs_submission" wjs-profile-project/wjs --include=test_*.py`). Expected PASS, no new failures vs. baseline.
- [ ] **Step 2:** Add a changelog note in each repo's `CHANGELOG.md` (removed `*_MISSING_FIELDS_FUNCTION` settings and `*_missing_fields` functions; `validation(user, journal)` contract; new `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`). Commit (after approval) `docs: changelog for required-fields dict`.
