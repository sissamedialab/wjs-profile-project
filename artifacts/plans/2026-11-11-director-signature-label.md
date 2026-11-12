# Director signature label — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Write a message" and "select new editor" sign with the same role words, and directors sign as "Director"
everywhere except JCOM and JCOMAL.

**Architecture:** A new boolean `wjs_review` setting, `directors_sign_as_director`, defaults to true. One helper in
`communication_utils` decides the signature role for a sender on an article. `WriteMessage.get_initial_body` and
`SupervisorAssignEditorForm.get_new_editor_message_context` both call it. Data migration 0024 sets the setting to false
for JCOM and JCOMAL.

**Tech Stack:** Django, Janeway `core.Setting`/`SettingValue`, `utils.setting_handler.get_setting`, pytest-django.

**Spec:** `artifacts/specs/2026-11-11-director-signature-label-design.md`

## Global Constraints

- Branch: `bugfix/issue-3187-director-signature-label`, from an up-to-date `wjs-develop`.
- Setting: name `directors_sign_as_director`, group `wjs_review`, types `boolean`, `is_translatable: False`, and
  default value `"on"`. Janeway's `processed_value` treats only `"on"` as true for booleans. Pretty name: "Directors
  sign as Director". Description: "If true, all directors sign their messages as 'Director'; otherwise the main
  director signs as 'Editor-in-chief' and the other directors as 'Deputy Editor'."
- Role words, exactly: `Editorial Office`, `Editor-in-charge`, `Director`, `Editor-in-chief`, `Deputy Editor`, and `""`
  for anyone else.
- Order: EO → editor-in-charge → main director → other director → `""`. The permission functions are
  `has_eo_role_by_article`, `is_article_editor`, `has_main_director_role_by_article` and `has_director_role_by_article`.
- Template variable `sender_role_label` keeps its name. Stored setting texts and migration 0023 are not touched.
- Journal codes `JCOM` and `JCOMAL` appear only in migration 0024.
- Tests run from `janeway/src`: `python -m pytest -p no:freezegun ../../wjs-profile-project/<path>`.
- The test journal fixture has code `JCOM` (`jcom_profile/tests/conftest.py:79`), but the tests' settings come from
  `set_default_plugin_settings`, so the default ("Director") applies unless a test creates the override.
- Changes stay in the working tree. Commit only when the developer approves.
- Comments and docstrings in English.

## Review Focus

- A director who is also the current editor-in-charge (the JCOM case with the section-editor role) signs as
  "Editor-in-charge" in both settings states. Tested in Task 1.
- A main director who is a past editor still signs as a director ("Director" by default, "Editor-in-chief" when the
  setting is false), not as "past editor". Tested in Tasks 1 and 2.
- An EO user who also holds the director role signs as "Editorial Office". Tested in Task 2 (existing test, updated).
- A DB with neither JCOM nor JCOMAL (a fresh test DB, other installs): migration 0024 is a no-op and creates nothing.
  Tested in Task 3.
- 0024 runs before `install()` has created the setting, for example on a fresh deploy. The migration creates the
  `Setting` row itself, and a later `install()` adds the default value without touching the overrides. Tested in Task 3.

---

### Task 1: Setting and shared helper, used by "write a message"

**Files:**
- Modify: `wjs/plugins/wjs_review/plugin_settings.py`: add `directors_sign_as_director()` next to
  `author_can_contact_director()` (≈ line 1024), and register it where that one is (≈ line 2406).
- Modify: `wjs/plugins/wjs_review/communication_utils.py`: add the helper.
- Modify: `wjs/plugins/wjs_review/views.py:2616-2652`: `get_initial_body` uses the helper.
- Test: `wjs/plugins/wjs_review/tests/test_communications.py`: `test_message_initial_body` (≈ line 1679) and a new
  helper test.

**Interfaces:**
- Produces: `communication_utils.sender_signature_role(workflow: ArticleWorkflow, user: Account) -> str`, which
  returns one of the role words in Global Constraints.
- Produces: setting `wjs_review.directors_sign_as_director`, read with
  `get_setting("wjs_review", "directors_sign_as_director", journal).processed_value`.

- [ ] **Step 1: Write the failing tests**

`test_sender_signature_role` (parametrize over `directors_sign_as_director` in `{True, False}`; for False, create a
`SettingValue(journal=article.journal, value="")` override). Fixtures as in `test_message_initial_body`.

```python
expected_main, expected_other = ("Director", "Director") if sign_as_director else ("Editor-in-chief", "Deputy Editor")
wf = assigned_article.articleworkflow
assert sender_signature_role(wf, eo_user.janeway_account) == "Editorial Office"
assert sender_signature_role(wf, editor) == "Editor-in-charge"
assert sender_signature_role(wf, main_director.janeway_account) == expected_main
assert sender_signature_role(wf, director.janeway_account) == expected_other
assert sender_signature_role(wf, author) == ""
# section-editor role added to both directors (JCOM case) → unchanged results
# main director made a past editor (PastEditorAssignment, as in test_message_initial_body) → still expected_main
```

Update `test_message_initial_body`. With the default, main director and director bodies contain
`f"{code} Director"`. Then create the JCOM override (false) and assert the current "Editor-in-chief" and "Deputy
Editor" checks, including the section-editor and past-editor sub-cases. EO, editor and author assertions are
unchanged.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_communications.py -k "sender_signature_role or message_initial_body" -v`
Expected: FAIL. ImportError for `sender_signature_role`, and "Director" is missing from the default body.

- [ ] **Step 3: Implement**

- Add the setting in `plugin_settings.py` (Global Constraints values; default value `"on"`).
- `sender_signature_role` in `communication_utils.py`, following the spec table. Import `permissions` inside the
  function if a module-level import causes a cycle.
- `get_initial_body`: compute `role = sender_signature_role(...)`. Return `""` when it is empty. Otherwise keep the
  current HTML (EO adds `{self.request.user}<br>`) with the line `f"{self.request.journal.code} {role}"`.

- [ ] **Step 4: Run to verify they pass**

Same command. Expected: all pass. Then run the whole `test_communications.py`. Expected: no new failures.

### Task 2: "Select new editor" uses the helper

**Files:**
- Modify: `wjs/plugins/wjs_review/forms.py:1510-1522` (`get_new_editor_message_context`). Remove the `role_label`
  import if it becomes unused.
- Test: `wjs/plugins/wjs_review/tests/test_editor_assignment_signature.py` (the 3 form tests at lines 32-54).

**Interfaces:**
- Consumes: `communication_utils.sender_signature_role` (Task 1).
- Produces: `sender_role_label` in the context is `sender_signature_role(self.instance, self.request.user)`.

- [ ] **Step 1: Update the failing tests**

- `test_signature_shows_eo_label`: `f"{code} Editorial Office" in body`, and `"role.slug" not in body`.
- `test_signature_shows_director_label`: `f"{code} Director" in body` (default).
- New `test_signature_main_director_without_setting`: with the journal override false, `main_director` →
  `f"{code} Editor-in-chief"`, and `director` → `f"{code} Deputy Editor"`.
- `test_signature_eo_wins_over_director`: `f"{code} Editorial Office" in body`, and `"Director" not in body`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_editor_assignment_signature.py -v`
Expected: the form tests FAIL (body says "EO"). The migration tests pass.

- [ ] **Step 3: Implement.** Replace the `role_label(role_for_article(...))` value with the helper and update the
  docstring example to "Editorial Office", "Director".

- [ ] **Step 4: Run to verify they pass.** Same command. Expected: all pass.

### Task 3: Data migration 0024 for JCOM and JCOMAL

**Files:**
- Create: `wjs/plugins/wjs_review/migrations/0024_directors_sign_as_director.py`
- Test: `wjs/plugins/wjs_review/tests/test_editor_assignment_signature.py`

**Interfaces:**
- Produces (module-level, imported in tests through `importlib`, as for 0023): `JOURNAL_CODES = ("JCOM", "JCOMAL")`,
  `forwards(apps, schema_editor) -> None`, `backwards(apps, schema_editor) -> None`.
- Dependencies: `("wjs_review", "0023_editor_assignment_signature_role")`,
  `("core", "0110_one_click_review_on_by_default")`, and the latest `journal` migration that 0023's graph already
  reaches.

- [ ] **Step 1: Write the failing tests**

- `test_0024_creates_false_overrides`: create a journal with code `JCOMAL` besides the fixture `JCOM`, plus one
  other journal (`JCAP`). After `forwards`, `get_setting(...).processed_value` is False for JCOM and JCOMAL and True
  for JCAP. `SettingValue` rows with a journal exist only for JCOM and JCOMAL.
- `test_0024_is_idempotent`: `forwards` twice gives one override per journal, with value `""`.
- `test_0024_backwards_removes_overrides`: `forwards` then `backwards` leaves no journal overrides, and the default
  value is untouched.
- `test_0024_without_target_journals_is_noop`: rename the fixture journal's code to `JCAP`, delete the setting, run
  `forwards`, and check that no `Setting` or `SettingValue` named `directors_sign_as_director` exists.
- `test_0024_before_install`: delete the setting, run `forwards` (JCOM exists), then run
  `set_default_plugin_settings(force=True)`. JCOM reads False, and a journal without an override reads True.

- [ ] **Step 2: Run to verify they fail.** Same file command. Expected: `ModuleNotFoundError` for 0024.

- [ ] **Step 3: Implement**

`forwards`: use historical models (`journal.Journal`, `core.SettingGroup`, `core.Setting`, `core.SettingValue`). Find
journals with `code__in=JOURNAL_CODES`, and return if there are none. Then `get_or_create` the `wjs_review` group and
the `Setting`, with the Global Constraints fields so `install()` finds it. For each journal, `update_or_create` its
`SettingValue` with `value=""`. Setting this value is enough to make `processed_value` false.
`backwards`: delete the overrides of `directors_sign_as_director` for those journals.
`operations = [migrations.RunPython(forwards, backwards)]`.

- [ ] **Step 4: Run to verify they pass**

Run the new test file, then from `janeway/src`:
- `python manage.py migrate wjs_review` on the local DB. Then `get_setting` reads False for JCOM and JCOMAL, and True
  for the other five journals.
- `python manage.py migrate wjs_review 0023` followed by `migrate wjs_review` (reverse and forward, both clean).

### Wrap-up (after Tasks 1–3)

- [ ] Full `wjs_review` suite: no new failures. Run pre-commit on the changed files.
- [ ] Review, code-eval, activity log and MR follow the earlier flow, each only with the developer's approval.
  The MR flags migration 0024 for reviewer confirmation, together with the decision that default-on means new
  journals sign as "Director".
