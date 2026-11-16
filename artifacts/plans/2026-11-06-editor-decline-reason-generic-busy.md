# Generic "busy" decline reason Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Editor decline reason `JCOM_BUSY` ("Already too busy with JCOM editorial work") with a generic `BUSY_EDITORIAL` ("Too busy with other editorial tasks") on every journal, migrating stored rows.

**Architecture:** One `TextChoices` member swap in `PastEditorAssignment.DeclineReasons`; form and Director notification already derive from `DeclineReasons.choices`. One `wjs_review` migration (AlterField choices + reversible RunPython data move). In-place edits of the en/es/pt catalogs plus recompiled `.mo`.

**Tech Stack:** Django 5.2, pytest-django, gettext catalogs.

**Spec:** `artifacts/specs/2026-11-06-editor-decline-reason-generic-busy-design.md`

## Global Constraints

- New code: `BUSY_EDITORIAL`; new label (exact): `Too busy with other editorial tasks`.
- `JCOM_BUSY` is removed from `DeclineReasons`; no label may contain "JCOM".
- `BUSY_EDITORIAL` takes `JCOM_BUSY`'s position (right after `EMPTY`); other reasons unchanged.
- Already-sent Director messages are not touched.
- Branch: `feature/issue-3306-generic-busy-decline-reason`; MR uses `main: #3306`, no closing keywords.
- Tests run from the usual Janeway test setup (`pytest` with `wjs.defaults.tests`).

## Review Focus

1. Rows with other reasons (`BUSY`, `OTHER`, `NULL`, legacy free-text labels written by `import_wjapp`) must be left untouched by the data migration — pinned in Task 1 migration test.
2. Reverse migration must restore `JCOM_BUSY` exactly — pinned in Task 1.
3. `makemigrations --check` must show no pending changes after the choices swap — Task 1 Step 4.
4. es/pt users must see a translated label, not the English fallback — Task 2 Step 3.
5. A form posting the stale `JCOM_BUSY` value (open modal during deploy) is rejected as invalid choice rather than stored — pinned in Task 1 form test.

---

### Task 1: `BUSY_EDITORIAL` choice + migration

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py:2010` (`PastEditorAssignment.DeclineReasons`)
- Create: `wjs/plugins/wjs_review/migrations/0025_editor_decline_reason_busy_editorial.py`
- Test: `wjs/plugins/wjs_review/tests/test_decline_reasons.py` (new)

**Interfaces:**
- Produces: `PastEditorAssignment.DeclineReasons.BUSY_EDITORIAL == "BUSY_EDITORIAL"`; migration module exposes `forwards(apps, schema_editor)` and `backwards(apps, schema_editor)`, constants `OLD_CODE = "JCOM_BUSY"`, `NEW_CODE = "BUSY_EDITORIAL"`.

- [ ] **Step 1: Write the failing tests** in `test_decline_reasons.py` (module docstring referencing specs#3306; follow `test_editor_assignment_signature.py` for the migration-test style: `importlib.import_module(MIGRATION_MODULE).forwards(django_apps, None)` with `MIGRATION_MODULE = "plugins.wjs_review.migrations.0025_editor_decline_reason_busy_editorial"`):
  - `test_decline_reasons_offer_generic_busy_editorial`: `dict(DeclineReasons.choices)["BUSY_EDITORIAL"] == "Too busy with other editorial tasks"`; `"JCOM_BUSY" not in DeclineReasons.values`; no label contains `"JCOM"`; `DeclineReasons.values[1] == "BUSY_EDITORIAL"`.
  - `test_decline_form_rejects_stale_jcom_busy` (fixtures `assigned_article`, `section_editor`, `fake_request`): `EditorDeclinesAssignmentForm(data={"decline_reason": "JCOM_BUSY"}, request=fake_request, instance=assigned_article)` is not valid with an error on `decline_reason`; with `"BUSY_EDITORIAL"` it is valid.
  - `test_editor_decline_busy_editorial_notifies_director` (fixtures `assigned_article`, `section_editor`, `director`, `fake_request`; `fake_request.user = section_editor.janeway_account`): run `HandleEditorDeclinesAssignment(..., form_data={"decline_reason": DeclineReasons.BUSY_EDITORIAL, "decline_text": ""})`; assert the returned `PastEditorAssignment.decline_reason == "BUSY_EDITORIAL"`; assert the latest `Message` for the article addressed to the director has `"Too busy with other editorial tasks"` in its body and no `"JCOM editorial work"`. (If the `director` fixture is not the one `get_director_user` returns, use whatever the existing decline tests in `test_logic.py` use for the director; stop and report if none produces a message.)
  - `test_migration_moves_jcom_busy_to_busy_editorial`: create `PastEditorAssignment` rows for `assigned_article` / `section_editor` with `decline_reason` in `"JCOM_BUSY"`, `"BUSY"`, `"OTHER"`, `None`, `"Too busy in general"` (bulk create / `update()` to bypass choices); run `forwards`; assert only the first became `"BUSY_EDITORIAL"`, the rest unchanged.
  - `test_migration_backwards_restores_jcom_busy`: row with `"BUSY_EDITORIAL"` → `backwards` → `"JCOM_BUSY"`; other values unchanged.

- [ ] **Step 2: Run to verify they fail**
  Run: `pytest wjs/plugins/wjs_review/tests/test_decline_reasons.py -v`
  Expected: FAIL (missing `BUSY_EDITORIAL`, migration module not found).

- [ ] **Step 3: Implement**
  - In `DeclineReasons`, replace the `JCOM_BUSY` line with `BUSY_EDITORIAL = "BUSY_EDITORIAL", _("Too busy with other editorial tasks")`.
  - Migration `0024`: dependency `("wjs_review", "0023_editor_assignment_signature_role")`; module docstring explaining specs#3306; `forwards`/`backwards` do a single `PastEditorAssignment.objects.filter(decline_reason=OLD).update(decline_reason=NEW)` via `apps.get_model("wjs_review", "PastEditorAssignment")`; operations = `AlterField` generated by `makemigrations` (copy its field definition) + `RunPython(forwards, backwards)`.

- [ ] **Step 4: Run to verify they pass**
  Run: `pytest wjs/plugins/wjs_review/tests/test_decline_reasons.py -v` — Expected: all passed.
  Run: `python manage.py makemigrations wjs_review --check --dry-run` (Janeway `src/`) — Expected: "No changes detected".
  Run: `pytest wjs/plugins/wjs_review/tests/test_appeal_permissions.py wjs/plugins/wjs_review/tests/test_logic.py -k "decline" -q` — Expected: no new failures.

- [ ] **Step 5: Commit** (only if per-change commits were chosen in flow step 4)
  `feat(wjs_review): replace JCOM-specific decline reason with a generic one`

### Task 2: Translation catalogs

**Files:**
- Modify: `wjs/locale/{en,es,pt}/LC_MESSAGES/django.po` and the matching `django.mo`

**Interfaces:**
- Consumes: label `Too busy with other editorial tasks` from Task 1.

- [ ] **Step 1: Edit in place** (do not run a full `makemessages`: source references are stale and it would churn the whole catalog). In each of the three `.po` files, replace the `msgid "Already too busy with JCOM editorial work"` entry with `msgid "Too busy with other editorial tasks"`, keeping the existing `#:` reference line, and set:
  - en: `msgstr ""`
  - es: `msgstr "Demasiado ocupado con otras tareas editoriales"`
  - pt: `msgstr "Muito ocupado com outras tarefas editoriais"`

- [ ] **Step 2: Recompile** each catalog: `msgfmt -c -o wjs/locale/<lang>/LC_MESSAGES/django.mo wjs/locale/<lang>/LC_MESSAGES/django.po` for en, es, pt — Expected: exit 0, no errors.

- [ ] **Step 3: Verify**
  Run: `grep -rn "JCOM editorial work" wjs/` — Expected: only `migrations/0001_initial.py` (historical, left as is).
  Run a one-liner with `translation.override("es")` / `("pt")` printing `PastEditorAssignment.DeclineReasons.BUSY_EDITORIAL.label` — Expected: the es/pt strings above. (Optionally add this as `test_busy_editorial_label_is_translated` parametrized over es/pt in `test_decline_reasons.py` if the test settings load `wjs/locale`.)

- [ ] **Step 4: Commit** (only if per-change commits were chosen)
  `chore(i18n): translate the generic busy decline reason`

### After implementation (not code)

- Run the migration on the test environment data, confirm `PastEditorAssignment.objects.filter(decline_reason="JCOM_BUSY").count() == 0`, report on wjs/specs#3306.
- Run the issue's functional steps on JCAP and JCOM.
