# Per-journal professional links in correspondence-author eligibility: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eligibility as correspondence author counts only the professional-link fields each journal shows, and a startup check plus a test keep the required-fields rules and `PROFILE_FIELDS` consistent.

**Architecture:** Split the shared `_PROFESSIONAL_LINKS_ENTRY` in `wjs/defaults/settings.py` into one entry per journal (data only; validation and profile highlights already read the entry). Add a pure consistency function in a new `wjs/jcom_profile/checks.py`, wrapped by a registered Django system check returning `Error`.

**Tech Stack:** Django system checks (`django.core.checks`), pytest-django, wjs-submission's `missing_required_fields` / `get_required_field_entries`.

**Spec:** `artifacts/specs/2026-11-23-per-journal-link-eligibility-design.md`; functional spec and test steps in wjs/specs#3392.

**Working tree:** worktree `.worktrees/bugfix/issue-3392-per-journal-link-eligibility`, branch `bugfix/issue-3392-per-journal-link-eligibility`, cut from `origin/wjs-develop` (5886360a). Commit following the strategy chosen in flow step 4; no step below commits on its own.

## Global Constraints

- wjs-submission must not depend on `wjs.jcom_profile` (specs #30, #3033): no change in wjs-submission; the check lives in this repo.
- A `jcomprofile.*` path stays first in each links entry (a user without a JCOMProfile must raise, not pass through a social handle).
- `PROFESSIONAL_LINKS_LABEL` and `PROFESSIONAL_LINKS_GROUP_HELP` (`wjs/jcom_profile/constants.py`) are unchanged: same label and help text for every journal.
- JCOM and JCOMAL share one entry; the `None` fallback of the rules dict is untouched (no links required).
- Visible link fields per `PROFILE_FIELDS`: JCOM/JCOMAL `facebook`, `twitter`, `linkedin`, `records_other`; JCAP `records_arxiv`, `records_inspire`, `records_scix`, `records_other`.
- The check resolves rules and visible fields with the same fallback as the runtime: `rules.get(code, rules[None])` and `PROFILE_FIELDS.get(code, PROFILE_FIELDS[None])` (`is_field_available`, `wjs/jcom_profile/templatetags/wjs_tags.py`).
- Tests run from `janeway/src` (see `.claude/rules/tests.md`); prefer single files, never the full suite.

## Review Focus

1. A JCAP user with a social handle AND a records value stays eligible (only the visible field counts, the hidden one is just ignored). Pinned in Task 1.
2. A whitelisted user whose only link is a hidden field stays eligible. Pinned in Task 1.
3. A journal that uses the `None` fallback rules (JQuant) must not be asked for the links entry. Pinned in Task 1.
4. A required-fields entry without a profile hint or with empty `profile.fields` (active account, affiliation) must not be reported by the check. Pinned in Task 2.
5. A journal present only in `PROFILE_FIELDS` (JQuant) or only in the rules dict is checked through the fallbacks, not skipped or raising `KeyError`. Pinned in Task 2.

---

### Task 0: Point the environment at the worktree (needs the user's go-ahead)

The venv's editable installs and the `janeway/src/plugins/*` symlinks point at the main `wjs-profile-project` checkout, so tests run from `janeway/src` would test the wrong code.

- [ ] **Step 1: Ask the user for permission to run the switch script** (it changes shared venv state and `janeway/src` symlinks). Do not run it without a yes.
- [ ] **Step 2: Run** `/home/yakky/Projects/projects/sissa-1.8/switch-wjs-plugin.sh wjs-profile-project /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project/.worktrees/bugfix/issue-3392-per-journal-link-eligibility`, then `... status wjs-profile-project`.
  Expected: every `plugins/wjs_*` symlink and the venv editable location point at the worktree.
- [ ] **Step 3: Baseline.** From `janeway/src`: `pytest ../../wjs-profile-project/.worktrees/bugfix/issue-3392-per-journal-link-eligibility/wjs/jcom_profile/tests/test_account_validation.py ../../wjs-profile-project/.worktrees/bugfix/issue-3392-per-journal-link-eligibility/wjs/jcom_profile/tests/test_profile_required_fields.py --create-db`.
  Expected: PASS. If anything fails on the untouched branch, stop and report it.
- [ ] **At the end of the flow:** restore with the same script (`... wjs-profile-project /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project`), after asking.

### Task 1: Per-journal professional links entries

**Files:**
- Modify: `wjs/defaults/settings.py` (the `_PROFESSIONAL_LINKS_ENTRY` block, about l.808-835, `_JCOM_REQUIRED_FIELDS`, and the `"JCAP"` list of `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`)
- Test: `wjs/jcom_profile/tests/test_account_validation.py`, `wjs/jcom_profile/tests/test_profile_required_fields.py`

**Interfaces:**
- Produces: module constants `_JCOM_PROFESSIONAL_LINKS_ENTRY` and `_JCAP_PROFESSIONAL_LINKS_ENTRY` (type `ProfileRequiredFieldEntry`); `_PROFESSIONAL_LINKS_ENTRY` no longer exists. Task 2 relies only on the shipped dict, not on these names.

- [ ] **Step 1: Update the existing tests to the new rules (they must then fail).** In `test_account_validation.py`:
  - `_make_jcom_valid`: replace the valid link `records_scix="https://scixplorer.org/author/doe"` with `records_other="https://example.org/doe"` (and `records_scix=""`), because a SciX record no longer makes a JCOM user valid.
  - Replace `_has_professional_links(user)` with `_has_jcom_links(user)` (true for `records_other`, `facebook`, `twitter`, `linkedin`) and `_has_jcap_links(user)` (true for `records_scix`, `records_inspire`, `records_arxiv`, `records_other`); use them in `_old_jcom_validation`/`_old_jcom_missing_fields` and `_old_jcap_validation`/`_old_jcap_missing_fields` respectively. Adjust the docstrings: "as in f84bae5c, links restricted to the fields the journal shows (#3392)".
  - In `test_profile_required_fields.py` change `assert len(links.fields) == 7` (around l.101, JCOM journal) to `assert set(links.fields) == {"records_other", "facebook", "twitter", "linkedin"}`.
- [ ] **Step 2: Add the new failing tests** in `test_account_validation.py`:
  - `test_jcom_validation_hidden_records_alone_fail` (parametrized over `records_scix`, `records_inspire`, `records_arxiv`, and over journal codes `JCOM`, `JCOMAL`): `_make_jcom_valid`, `_clear_links`, set only that JCOMProfile field, assert `not jcom_correspondence_author_validation(...)` and that `str(PROFESSIONAL_LINKS_LABEL)` is in `[str(l) for l in missing_required_fields(user, _journal(code))]`.
  - `test_jcap_validation_hidden_social_handles_alone_fail` (parametrized over `facebook`, `twitter`, `linkedin`): same with `_make_jcap_valid`, assert it fails and the label is listed.
  - `test_jcap_validation_each_records_field_alone_passes` (parametrized over `records_scix`, `records_inspire`, `records_arxiv`, `records_other`) and `test_jcom_validation_each_visible_link_alone_passes` (over `records_other`, `facebook`, `twitter`, `linkedin`).
  - Review Focus 1, `test_jcap_validation_social_handle_and_records_passes`: records field set plus `facebook` set, assert eligible.
  - Review Focus 2, `test_whitelisted_user_with_hidden_link_only_is_eligible` (`@pytest.mark.django_db`, uses the `journal_factory` fixture): JCAP journal, user with only `facebook`; assert `not is_user_eligible_for_correspondence_author(jcap, user)` first, then create `WhitelistedCorrespondenceAuthors(user=user, journal=jcap)` (validity dates empty) and assert it is True. `is_user_eligible_for_correspondence_author` and `WhitelistedCorrespondenceAuthors` come from `plugins.wjs_submission`.
  - Review Focus 3, `test_fallback_journal_does_not_require_links`: `missing_required_fields(user, _journal("JQuant"))` contains no `str(PROFESSIONAL_LINKS_LABEL)` for a user without any link.
  - Structural: `test_links_entries_keep_a_jcomprofile_path_first`: for `JCOM`, `JCOMAL`, `JCAP`, the links entry (the one with `profile.section == "core_edit_profile_additional"`) has `paths[0].startswith("jcomprofile.")`.
  In `test_profile_required_fields.py`, `test_requirements_jcap_links_entry_names_only_records_fields` (`@pytest.mark.django_db`, `journal_factory("JCAP")`): the links entry returned by `get_correspondence_author_requirements(_request(user, jcap))` has `set(fields) == {"records_scix", "records_inspire", "records_arxiv", "records_other"}`.
- [ ] **Step 3: Run and see failures.** From `janeway/src`: `pytest <worktree>/wjs/jcom_profile/tests/test_account_validation.py <worktree>/wjs/jcom_profile/tests/test_profile_required_fields.py`.
  Expected: the new and updated link tests FAIL (shared entry still counts all seven fields); unrelated tests pass.
- [ ] **Step 4: Implement the split in `wjs/defaults/settings.py`.** Replace `_PROFESSIONAL_LINKS_ENTRY` with:
  - `_JCOM_PROFESSIONAL_LINKS_ENTRY`: `label=PROFESSIONAL_LINKS_LABEL`; `paths=["jcomprofile.records_other", "facebook", "twitter", "linkedin"]`; `profile={"section": "core_edit_profile_additional", "group_help": PROFESSIONAL_LINKS_GROUP_HELP, "fields": ["records_other", "facebook", "twitter", "linkedin"]}`.
  - `_JCAP_PROFESSIONAL_LINKS_ENTRY`: same label and profile section/group_help; `paths=["jcomprofile.records_scix", "jcomprofile.records_inspire", "jcomprofile.records_arxiv", "jcomprofile.records_other"]`; `profile.fields=["records_scix", "records_inspire", "records_arxiv", "records_other"]`.
  Use the first in `_JCOM_REQUIRED_FIELDS`, the second in the `"JCAP"` list. Replace the comment "All the link fields count for every journal ... Accepted limitation" with a one-line note that each journal lists only the link fields it shows (`PROFILE_FIELDS`), kept consistent by the check in `wjs/jcom_profile/checks.py`.
- [ ] **Step 5: Run the same command.** Expected: PASS. Also run `pytest <worktree>/wjs/jcom_profile/tests/test_account_validation.py::test_required_fields_setting_shape` (still `override["JCOM"] == override["JCOMAL"]`).

### Task 2: Consistency check between required-fields rules and `PROFILE_FIELDS`

**Files:**
- Create: `wjs/jcom_profile/checks.py`, `wjs/jcom_profile/tests/test_checks.py`
- Modify: `wjs/jcom_profile/apps.py` (`JCOMProfileConfig.ready()`: add `checks` to the existing `from wjs.jcom_profile import forms, signals, tracing_djangoq, urls  # NOQA` import so the registration runs)

**Interfaces:**
- Produces: `find_required_fields_inconsistencies(required_fields: Mapping[str | None, Sequence[Mapping]], profile_fields: Mapping[str | None, Sequence[str]]) -> list[str]` (pure; empty list means consistent) and `check_correspondence_author_required_fields(app_configs, **kwargs) -> list[checks.Error]`, registered with `@register()`, error id `wjs_profile.E001`.
- Consumes: the shipped `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` and `PROFILE_FIELDS` (Task 1 made them consistent).

- [ ] **Step 1: Write the failing tests in `test_checks.py`.**
  - `test_shipped_settings_are_consistent`: `find_required_fields_inconsistencies(settings.SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS, settings.PROFILE_FIELDS) == []`.
  - `test_hidden_field_in_a_rule_is_reported`: rules `{None: [], "JCAP": [entry]}` where `entry = {"label": "Links", "paths": ["facebook"], "profile": {"section": "s", "fields": ["facebook"]}}` and `profile_fields = {None: ("facebook",), "JCAP": ("records_scix",)}`; assert exactly one message and that it contains `"JCAP"`, `"Links"` and `"facebook"`.
  - `test_paths_and_profile_fields_must_match`: entry with `paths=["jcomprofile.records_scix", "facebook"]` and `profile.fields=["records_scix"]`, all shown by the journal; assert one message mentioning the entry label.
  - Review Focus 4, `test_entries_without_a_profile_hint_or_fields_are_skipped`: entries `{"label": "Active", "paths": ["is_active"]}` and `{"label": "Affiliation", "paths": ["affiliation"], "profile": {"section": "a", "fields": []}}` give `[]` even though `is_active`/`affiliation` are in no `PROFILE_FIELDS`.
  - Review Focus 5, `test_journals_are_resolved_through_the_fallbacks`: rules `{None: [bad_entry]}` and `profile_fields = {None: ("email",), "JQuant": ("email",)}` where `bad_entry` names `facebook`: the function reports per journal code, no deduplication: assert one message starting with `"default"` and one starting with `"JQuant"` (a code present only in `profile_fields` resolves its rules through `required_fields[None]`). A journal only in the rules (`"JCOM"` with `profile_fields` lacking it) is checked against `profile_fields[None]`.
  - `test_registered_check_returns_errors`: with the `settings` fixture, set `settings.PROFILE_FIELDS = {**settings.PROFILE_FIELDS, "JCAP": tuple(f for f in settings.PROFILE_FIELDS["JCAP"] if f != "records_arxiv")}`; assert `check_correspondence_author_required_fields(None)` returns a list of `django.core.checks.Error` with `id == "wjs_profile.E001"` mentioning `records_arxiv`; and with the shipped settings it returns `[]`.
- [ ] **Step 2: Run and see failures.** From `janeway/src`: `pytest <worktree>/wjs/jcom_profile/tests/test_checks.py`. Expected: FAIL (ImportError: module `wjs.jcom_profile.checks` does not exist).
- [ ] **Step 3: Implement `find_required_fields_inconsistencies` in `wjs/jcom_profile/checks.py`.** Algorithm: for every `code` in `set(required_fields) | set(profile_fields)`, `entries = required_fields.get(code, required_fields[None])`, `shown = profile_fields.get(code, profile_fields[None])`; for each entry with non-empty `entry.get("profile", {}).get("fields")`: report every field not in `shown`; and report when `{path.rsplit(".", 1)[-1] for path in entry["paths"]} != set(profile fields)`. Messages start with the journal code (`"default"` for `None`) and contain the entry label and the offending field names. Return messages sorted for stable output.
- [ ] **Step 4: Implement the registered check** in the same module: `check_correspondence_author_required_fields` decorated with `django.core.checks.register()`, reading `plugins.wjs_submission.settings.CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` (the effective rules wjs-submission uses, same fallback as `get_required_field_entries`; import the module, not the name, so it is read at call time) and `django.conf.settings.PROFILE_FIELDS`, returning one `Error(message, id="wjs_profile.E001")` per message. Add `checks` to the import in `JCOMProfileConfig.ready()`.
- [ ] **Step 5: Run** `pytest <worktree>/wjs/jcom_profile/tests/test_checks.py`. Expected: PASS.
- [ ] **Step 6: Run the real check.** From `janeway/src`: `python manage.py check`. Expected: no `wjs_profile.E001`. Then, temporarily add `"facebook"` to the JCAP `paths` and `profile.fields` of `_JCAP_PROFESSIONAL_LINKS_ENTRY`, run `python manage.py check` and confirm the `wjs_profile.E001` Error names `JCAP` and `facebook`; revert the edit (`git diff` must show no change in `settings.py` from this step).

### Task 3: Verification of the whole change

**Files:** none new.

- [ ] **Step 1: Run the touched suites.** From `janeway/src`: `pytest <worktree>/wjs/jcom_profile/tests/test_account_validation.py <worktree>/wjs/jcom_profile/tests/test_profile_required_fields.py <worktree>/wjs/jcom_profile/tests/test_checks.py`. Expected: PASS.
- [ ] **Step 2: Lint.** From the worktree: `pre-commit run --all-files`. Expected: clean.
- [ ] **Step 3: Check nothing else changed behaviour.** Run `git diff --stat origin/wjs-develop`. Expected: only `settings.py`, `apps.py`, `checks.py` and the three test files (plus the spec and plan under `artifacts/`).
- [ ] **Step 4: Developer-only report for the issue** (not a code step): run the production-count queries listed in the issue's Technical notes if production access exists, and post the numbers in a comment on #3392.
