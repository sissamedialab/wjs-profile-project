# Profile required-fields highlight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an author reaches the profile from the submission, show which fields the journal requires (Required help text) and what is still missing (banner + tab badges), driven by the required-fields dict.

**Architecture:** S exposes two small public helpers over the dict. P adds a `profile` location hint to its dict entries, a request-level helper `get_submission_requirements(request)`, a `submission_requirements` form kwarg that turns help texts into `required_help_text(...)`, and a template tag feeding a banner and sidebar badges. Everything is recomputed per request; nothing is stored.

**Tech Stack:** Python 3, Django 5.2, django-bootstrap5, pytest-django. Repos: `wjs-submission-project` (S), `wjs-profile-project` (P); branch `feature/issue-2728-submission-required-highlight` in both.

**Spec:** `wjs-profile-project/artifacts/specs/2026-11-09-profile-required-fields-highlight-design.md`

## Global Constraints

- English docstrings/comments/commits; commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; commit strategy and approvals per the flow (decided in step 4); nothing is pushed.
- Everything new appears ONLY when `back_to_submission_url` logic holds (session `next`, same-host, safe), user authenticated, journal present, user NOT whitelisted. Outside it, forms and pages behave exactly as today.
- Dict entry hint: `"profile": {"section": <url name>, "fields": [<form field names>]}`; sections `core_edit_profile`, `core_edit_profile_affiliations`, `core_edit_profile_email`, `core_edit_profile_additional`. Entries without the key (e.g. "Active account") are never shown.
- Required help text = `plugins.wjs_submission.helpers.forms.required_help_text(existing)` -> **Required** - existing. For entries with more than one field, the text added before the existing help is `_("fill in at least one of these fields")`.
- Badge = number of missing hinted entries per section (an "any of" group counts 1). Banner names the journal and lists missing labels. Use Bootstrap classes (`alert alert-warning`, `badge text-bg-warning`); no changes outside the two repos (the profile CSS lives in wjs-themes: do not touch it). No field outline styling.
- S must not import `wjs.jcom_profile`. Run pytest from `janeway/src`: `pytest --create-db ../../<repo>/<path> -k <name>`; pre-commit per repo before committing. Templates follow `.claude/rules/templates-django.md`.

## Review Focus

1. No `next` in session (editor/reviewer/other author): no banner, no badge, no Required text change on any profile page.
2. External or unsafe `next` (e.g. `https://evil.example.com/`): treated as not coming from submission.
3. Whitelisted user: nothing shown (eligible regardless of fields).
4. After a save that fills the missing fields: banner and badges gone, Required help text still present.
5. Only `is_active` missing, or a journal code absent from the dict (falls back to `None` rules): no banner for the unhinted entry; the fallback rules apply.
6. Group entries: name (JCOM) and professional links count as 1 in the badge; every field of the group carries the "at least one" text.

---

### Task 1: S — public helpers over the dict

**Files:**
- Modify: `wjs-submission-project/wjs/plugins/wjs_submission/account_validation.py`, `tests/test_account_validation.py`

**Interfaces:**
- Produces: `get_required_field_entries(journal: Journal) -> list[dict]` (the journal's entries, `None` fallback); `is_required_field_entry_satisfied(user: Account, entry: dict) -> bool`; `is_whitelisted_correspondence_author(journal: Journal, user: Account) -> bool` (public name of today's `_is_whitelisted_correspondence_author`, callers updated). `missing_required_fields(user, journal)` keeps its behavior, rebuilt on the first two.

- [ ] **Step 1: Write failing tests** in `tests/test_account_validation.py`: `test_get_required_field_entries_journal_or_none_fallback` (patched dict as in the existing evaluator tests), `test_is_required_field_entry_satisfied_any_of_and_allowed` (an any-of entry and an `allowed` entry, reusing the existing stub-user style), `test_is_whitelisted_correspondence_author_public` (valid whitelist row -> True, expired -> False). Run: ImportError.
- [ ] **Step 2: Implement** the three public functions in `account_validation.py` (rename `_is_whitelisted_correspondence_author`, `_is_entry_satisfied`; update internal callers); `missing_required_fields` = labels of `get_required_field_entries(journal)` entries failing `is_required_field_entry_satisfied`.
- [ ] **Step 3: Run** `pytest --create-db ../../wjs-submission-project/tests/test_account_validation.py ../../wjs-submission-project/tests/test_views.py`: all pass, including the part-1 equivalence tests unchanged.

### Task 2: P — hints in settings and `get_submission_requirements`

**Files:**
- Create: `wjs-profile-project/wjs/jcom_profile/profile/required_fields.py`, `wjs/jcom_profile/tests/test_profile_required_fields.py`
- Modify: `wjs/defaults/settings.py` (the `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` entries), `wjs/jcom_profile/profile/views.py` (`back_to_submission_url` reuses the shared helper)

**Interfaces:**
- Consumes: Task 1 helpers.
- Produces: `safe_back_to_submission_url(request) -> str | None` (session `next`, same-host/scheme check; the view property delegates to it); `@dataclass(frozen=True) class RequiredEntry: label: str; section: str; fields: tuple[str, ...]; missing: bool`; `get_submission_requirements(request) -> list[RequiredEntry]` (`[]` unless the trigger holds, user authenticated, `request.journal` set and user not whitelisted; one item per hinted entry in dict order, `missing` from `is_required_field_entry_satisfied`).
- Hints in settings (JCOM/JCOMAL): Name -> `core_edit_profile` [last_name, first_name]; Email -> `core_edit_profile_email` [email]; Profession -> `core_edit_profile` [profession]; Biography -> `core_edit_profile` [biography]; links -> `core_edit_profile_additional` [records_scix, records_inspire, records_arxiv, records_other, facebook, twitter, linkedin]. JCAP: First name -> `core_edit_profile` [first_name]; Email as above; Affiliation -> `core_edit_profile_affiliations` []; links as above. `None`: Name, Email, Affiliation(institution) as above. "Active account": no hint.

- [ ] **Step 1: Write failing tests** (`test_profile_required_fields.py`, request built with `RequestFactory` + session): returns `[]` without `next`; `[]` with external `next`; `[]` for anonymous; `[]` for a whitelisted user; for a JCOM user missing biography and links with a valid `next`, returns the hinted entries in dict order with `missing` True only for those two and the links entry `fields` of length 7; JCAP journal user missing affiliation -> entry `section == "core_edit_profile_affiliations"`; only `is_active` missing -> no entry flagged missing; unknown journal code -> the `None` entries; assert the settings shape: every hint has a `section` in the four allowed names. Run: ImportError.
- [ ] **Step 2: Implement** `required_fields.py` and the settings hints; make `BaseProfileEditView.back_to_submission_url` call `safe_back_to_submission_url(self.request)`.
- [ ] **Step 3: Run** the new test file plus `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_views.py -k back_to_submission` (existing behavior unchanged) and `test_account_validation.py`: pass.

### Task 3: P — Required help text on the forms

**Files:**
- Modify: `wjs/jcom_profile/profile/forms.py`, `wjs/jcom_profile/profile/views.py` (`get_form_kwargs`), `wjs/jcom_profile/tests/test_profile_required_fields.py`

**Interfaces:**
- Consumes: `RequiredEntry`, `get_submission_requirements`.
- Produces: forms `WjsPersonalInfoForm`, `WjsEmailChangeForm`, `WjsAdditionalInfoForm` accept the kwarg `submission_requirements: list[RequiredEntry] | None` (popped in `__init__`, default `[]`); for each entry of the form's own section, every field named in `fields` gets `required_help_text(<existing help>)`; entries with more than one field prepend `_("fill in at least one of these fields")` to the existing help. Views `ProfilePersonalEditView`, `ProfileEmailEditView`, `ProfileAdditionalEditView` pass `get_submission_requirements(request)` through `get_form_kwargs` (a view class flag, `apply_submission_requirements = True`, defaulting to False on `BaseProfileEditView`).

- [ ] **Step 1: Write failing tests**: instantiate each form with requirements built by hand — Biography/Profession on the personal form get help text starting with `<strong>Required</strong>` (Biography keeps its existing sentence after it); group (links) fields on the additional form all contain the "at least one" text; entries of another section leave the form untouched; with no requirements the help texts equal today's (including the plain "Required" on required fields of the personal form). Through the client: `GET /<journal>/profile/` with session `next` shows `<strong>Required</strong>` for biography; without `next` it does not render the strong tag version. Run: FAIL.
- [ ] **Step 2: Implement** a small private helper in `forms.py` applying the rule, call it at the end of the three `__init__`s; wire `get_form_kwargs`.
- [ ] **Step 3: Run** the test file and `test_views.py -k profile`: pass.

### Task 4: P — banner and tab badges

**Files:**
- Create: `wjs/jcom_profile/templates/wjs/profile/includes/submission_requirements_banner.html`, tests in `test_profile_required_fields.py`
- Modify: `wjs/jcom_profile/templatetags/wjs_tags.py`, `wjs/jcom_profile/templates/wjs/profile/base.html`, `wjs/jcom_profile/templates/wjs/profile/personal_affiliations_edit.html`

**Interfaces:**
- Consumes: `get_submission_requirements`.
- Produces: simple tag `{% get_submission_requirements as reqs %}` (takes_context, computed once per request, cached on the request object) returning `{"entries": [...], "missing": [RequiredEntry...], "missing_by_section": {section: int}}` (empty structures when the trigger does not hold); banner include rendered above `{% block profile_form %}` only when `missing` is non-empty (names `journal.name`/code and lists the missing labels, `alert alert-warning`, translatable); in the sidebar, each tab link shows `<span class="badge text-bg-warning">N</span>` when `missing_by_section[section]` is non-zero; Affiliations page shows a one-line `<strong>Required</strong>` note above the list when an affiliations entry is present in `entries`.

- [ ] **Step 1: Write failing tests** (client): with session `next` and a JCOM user missing biography, profession and links: the profile page contains the banner with "Biography", "Profession" and the links label, a badge `2` on Personal Details and `1` on Additional information; Additional information page shows the same banner; the Affiliations page of a JCAP user missing affiliation shows the Required note and badge `1`; after posting the personal form with biography/profession filled (existing post helper), the next GET has no banner and no badge on Personal Details while the biography help still contains the Required strong tag; no `next` -> none of banner/badge/note appears, on profile, additional, newsletters and email pages; only `is_active` missing -> no banner. Run: FAIL.
- [ ] **Step 2: Implement** tag, banner template (include from `base.html`), sidebar badges (compute `missing_by_section` lookups in the template with the tag result), affiliations note.
- [ ] **Step 3: Run** `pytest --create-db ../../wjs-profile-project/wjs/jcom_profile/tests/test_profile_required_fields.py ../../wjs-profile-project/wjs/jcom_profile/tests/test_views.py ../../wjs-profile-project/wjs/jcom_profile/tests/test_account_validation.py`: pass; run djlint via pre-commit on the touched templates.

### Task 5: Cross-repo check

- [ ] **Step 1:** From `janeway/src` run `pytest --create-db -n7 ../../wjs-submission-project ../../wjs-profile-project/wjs/jcom_profile/tests/test_profile_required_fields.py ../../wjs-profile-project/wjs/jcom_profile/tests/test_views.py ../../wjs-profile-project/wjs/jcom_profile/tests/test_account_validation.py`: PASS, no new failures. Run `pre-commit run --all-files` in both repos.
