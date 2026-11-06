# Editor-assignment signature role — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The pre-filled "EO manually assigns an Editor" body signs as "<journal code> <role label>" (e.g. "JCAP EO", "JCAP Director") instead of a bare "JCAP".

**Architecture:** `SupervisorAssignEditorForm.get_new_editor_message_context()` gains a `sender_role_label` variable computed with the existing `role_for_article` + `role_label` helpers; the default setting text uses `{{ sender_role_label }}`; a `RunPython` data migration rewrites the dead `{{ request.user.role.slug }}` placeholder in every stored copy of the setting (default and journal overrides, all translated `value_*` columns).

**Tech Stack:** Django, Janeway `core.SettingValue` (django-modeltranslation `value_<lang>` columns), pytest-django.

**Spec:** `artifacts/specs/2026-11-03-editor-assignment-signature-role-design.md`

## Global Constraints

- Setting name: `editor_assignment_manual_body` (group `wjs_review`).
- Context variable name: `sender_role_label` (exactly); template placeholder `{{ sender_role_label }}`.
- Value: `role_label(communication_utils.role_for_article(article, request.user))` — add `role_label` to the existing `from wjs.jcom_profile.constants import ...` line in `forms.py`; `communication_utils` is already imported there.
- Old placeholder to replace: `{{ request.user.role.slug }}`; nothing else in any stored text may change.
- Migration: `wjs_review/0022_editor_assignment_signature_role.py`, depends on `("wjs_review", "0021_automaticassignmentaudit")` and `("core", "0110_one_click_review_on_by_default")`.
- Docs/comments/docstrings in English. The MR must flag the data migration as needing reviewer confirmation (wrap-up, not a code task).

## Review Focus

- EO user who also holds the director role → signature must say "EO" (role_for_article checks EO first). Test in Task 1.
- Stored text written without inner spaces (`{{request.user.role.slug}}`) → must also be replaced; the helper matches `\{\{\s*request\.user\.role\.slug\s*\}\}` (builder's call extending the spec's "literal substring", same intent). Test in Task 2.
- Translated `value_<lang>` columns that are `NULL`/empty → skipped without error. Test in Task 2.
- Journal override customised elsewhere but containing the placeholder → only the placeholder changes, rest byte-identical. Test in Task 2.
- Migration run twice → second run changes nothing. Test in Task 2.

---

### Task 1: `sender_role_label` in the form context and default text

**Files:**
- Modify: `wjs/plugins/wjs_review/forms.py:1507-1512` (`SupervisorAssignEditorForm.get_new_editor_message_context`)
- Modify: `wjs/plugins/wjs_review/plugin_settings.py:2046` (default value of `editor_assignment_manual_body`)
- Test: `wjs/plugins/wjs_review/tests/test_editor_assignment_signature.py` (new)

**Interfaces:**
- Produces: context key `sender_role_label: str` available to the `editor_assignment_manual_body` template; default text line `{{ article.journal.code }} {{ sender_role_label }}`.

- [ ] **Step 1: Write the failing tests** (fixtures as in `test_logic.py::test_assign_new_editor`: `review_settings` first, `article`, `fake_request`, `eo_user`, `director`, `normal_user` with `section-editor` role; set `article.articleworkflow.state = EDITOR_TO_BE_SELECTED`; `selectable_editors=Account.objects.get_editors_with_keywords(article)`; build an **unbound** form (no `data`))

```python
def _initial_body(article, user, fake_request):
    fake_request.user = user
    form = SupervisorAssignEditorForm(user=user, request=fake_request, instance=article.articleworkflow,
                                      selectable_editors=Account.objects.get_editors_with_keywords(article))
    return form.fields["note_for_new_editor"].initial

def test_signature_shows_eo_label(review_settings, article, fake_request, eo_user):
    body = _initial_body(article, eo_user.janeway_account, fake_request)
    assert f"{article.journal.code} EO" in body
    assert "role.slug" not in body

def test_signature_shows_director_label(review_settings, article, fake_request, director):
    body = _initial_body(article, director.janeway_account, fake_request)
    assert f"{article.journal.code} Director" in body

def test_signature_eo_wins_over_director(review_settings, article, fake_request, eo_user, journal):
    eo_user.add_account_role("director", journal)
    body = _initial_body(article, eo_user.janeway_account, fake_request)
    assert f"{article.journal.code} EO" in body
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest wjs/plugins/wjs_review/tests/test_editor_assignment_signature.py -v` (from the usual Janeway test setup)
Expected: FAIL — body ends with `"JCOM \n"` (no label).

- [ ] **Step 3: Implement** — add `"sender_role_label": role_label(communication_utils.role_for_article(self.instance.article, self.request.user))` to the dict returned by `get_new_editor_message_context` (update its docstring: it builds the context for `editor_assignment_manual_body`, not `unassign_editor`); in `plugin_settings.py` replace `{{ request.user.role.slug }}` with `{{ sender_role_label }}`.

- [ ] **Step 4: Run to verify they pass** — same command; Expected: 3 passed. Also run `pytest wjs/plugins/wjs_review/tests/test_logic.py -k "assign" -q` — Expected: no new failures.

- [ ] **Step 5: Commit** (only if per-change commits were chosen) — `fix(wjs_review): render the sender's role in the manual editor assignment signature`

### Task 2: data migration for stored copies

**Files:**
- Create: `wjs/plugins/wjs_review/migrations/0022_editor_assignment_signature_role.py`
- Test: `wjs/plugins/wjs_review/tests/test_editor_assignment_signature.py`

**Interfaces:**
- Consumes: placeholder name `sender_role_label` from Task 1.
- Produces (module-level, testable via `importlib.import_module("plugins.wjs_review.migrations.0022_editor_assignment_signature_role")`, like `0018` in `test_attentionconditions.py`):
  - `replace_placeholder(text: str | None, pattern: re.Pattern, replacement: str) -> str | None`
  - `forwards(apps, schema_editor) -> None` — old → `{{ sender_role_label }}`
  - `backwards(apps, schema_editor) -> None` — `{{ sender_role_label }}` → `{{ request.user.role.slug }}`
  - `operations = [migrations.RunPython(forwards, backwards)]`

- [ ] **Step 1: Write the failing tests**

```python
def test_migration_rewrites_default_and_overrides(review_settings, journal):
    # arrange: force old placeholder into the default value and create a journal override
    # with custom text + "{{request.user.role.slug}}" (no spaces) in `value` and a NULL value_<other lang>
    migration.forwards(django_apps, None)
    # assert: default and override contain "{{ sender_role_label }}", not "role.slug";
    #         override's other text is identical to before except the placeholder; NULL column stays NULL

def test_migration_leaves_values_without_placeholder_untouched(review_settings, journal):
    # override text without the placeholder -> unchanged after forwards()

def test_migration_is_idempotent(review_settings, journal):
    # forwards() twice -> same text as after one run

def test_migration_backwards(review_settings, journal):
    # forwards() then backwards() -> "{{ request.user.role.slug }}" restored
```

- [ ] **Step 2: Run to verify they fail** — Expected: `ModuleNotFoundError` for the 0022 module.

- [ ] **Step 3: Implement** — `forwards`/`backwards` get historical `core.SettingValue`, filter `setting__name="editor_assignment_manual_body"`, iterate the model's concrete fields whose name is `value` or starts with `value_`, apply `replace_placeholder`, and `save(update_fields=[...changed fields])` only when something changed.

- [ ] **Step 4: Run to verify they pass** — full new test file: Expected all passed. Then `python manage.py makemigrations wjs_review --check --dry-run` (Janeway `src/`): Expected "No changes detected"; and `python manage.py migrate wjs_review` on the local DB, then re-run the shell query from the brainstorming (JCOMAL/JCOM/JQuant/default) — Expected: all show `{{ sender_role_label }}`.

- [ ] **Step 5: Commit** (only if per-change commits were chosen) — `fix(wjs_review): migrate stored manual editor assignment bodies to sender_role_label`
