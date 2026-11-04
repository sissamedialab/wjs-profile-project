# Unify "Permanently disabled" editor label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a single label, "Permanently disabled", for editors who are self-disabled (`enabled=False`) or EO-disabled (`brake_on == -1`), everywhere the editor lists render them.

**Architecture:** Add one profile predicate, `JCOMProfile.is_permanently_disabled_as_editor(journal)`, that ORs the two existing predicates, and one template filter wrapping it. The two editor list templates use that filter and a single `<span>` with the new label, so a doubly-disabled editor shows the label once.

**Tech Stack:** Django templates and filters, pytest-django.

**Spec:** https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3333 (no separate design doc; the issue is the spec)

## Global Constraints

- New label text, exact: `Permanently disabled` (msgid via `{% translate %}`). The old msgids `Permanently self disabled` and `Disabled by EO` must no longer be used for editors.
- Branch `bugfix/issue-3333-unify-permanently-disabled-label`; Conventional Commits, English.
- Django templates: 2-space indent, double quotes in tags, named `endblock`s (`.claude/rules/templates-django.md`).
- Python: black at 119 columns, docstrings in imperative mood ending with a period.
- `.po` files contain no entries for these msgids (checked with grep), so no locale edits.

## Usage audit (done while planning)

| Location | Uses | Action |
|---|---|---|
| `wjs_review/assign_editor/elements/editor_list.html:12-13` | `is_enabled_as_editor` and `is_disabled_by_eo`, with two different labels | Replace both with the unified filter and one label |
| `wjs_review/journal_editors/elements/editor_parameters_list.html:10` | `is_enabled_as_editor` only. An EO-disabled editor shows **no** label here today | Use the unified filter, which fixes the missing case |
| `wjs_review/select_reviewer/elements/reviewers_list.html:19` | `user.is_available_as_reviewer`, "Permanently self disabled" | Reviewer flag, not an editor flag. Confirmed in scope: change to the same unified label (`{% translate "Permanently disabled" %}`), condition unchanged |
| `wjs_review/views.py:3669` | Docstring mentions the template's "self disabled"/"unavailable" badges | Reword the docstring |
| `wjs_review/templatetags/wjs_review.py:112,130` | Filters `is_enabled_as_editor` and `is_disabled_by_eo`, no remaining template users after this plan | Keep (public filters, docstring updates only); the new filter composes the profile methods |
| `jcom_profile/models.py:113,131` | Profile predicates | Unchanged; the new method composes them |
| `test_automatic_article_assignment.py:319`, `test_staff_parameters*.py` | Predicate semantics | Unchanged |

## Review Focus

- Editor with no `StaffWorkloadParameters` row: both predicates fall back to "disabled" (`is_enabled_as_editor` returns `False` on a missing row), so the label shows. Expected: label shown, no crash.
- Editor that is both self-disabled and EO-disabled: label appears exactly once.
- Editor on the Editor Management page with `brake_on == -1` and `enabled=True`: label appears (it was missing before).
- Fully enabled editor with `brake_on` of 0 or a positive value: no label on either page.
- Strings "Permanently self disabled" and "Disabled by EO" must not appear on either editor page, nor on the select-reviewer page.
- Select-reviewer page: the reviewer branch is an `elif` chain (author → vacancy → not available → inactive), so the label must still render only when no earlier branch matches.

---

### Task 1: Profile predicate and template filter

**Files:**
- Modify: `wjs/jcom_profile/models.py` (after `is_disabled_by_eo`, ~line 146)
- Modify: `wjs/plugins/wjs_review/templatetags/wjs_review.py` (after `is_disabled_by_eo`, ~line 145)
- Test: `wjs/jcom_profile/tests/test_staff_parameters.py`

**Interfaces:**
- Produces: `JCOMProfile.is_permanently_disabled_as_editor(self, journal: Journal) -> bool`
- Produces: template filter `is_permanently_disabled_as_editor(user: Account, journal: Journal) -> bool`, used as `{{ user|is_permanently_disabled_as_editor:journal }}` (needs `{% load wjs_review %}`)

- [ ] **Step 1: Write the failing test** `test_is_permanently_disabled_as_editor_covers_self_and_eo_disable` in `test_staff_parameters.py`, using the `editors` and `journal` fixtures and `StaffWorkloadParameters.objects.get(user=editor, journal=journal)`. Assert, with messages:
  - `enabled=True, brake_on=0` → `False`
  - `enabled=True, brake_on=5` → `False`
  - `enabled=False, brake_on=0` → `True`
  - `enabled=True, brake_on=-1` → `True`
  - `enabled=False, brake_on=-1` → `True`

  Add `test_is_permanently_disabled_as_editor_without_parameters`: delete the editor's `StaffWorkloadParameters` row, then assert `True`.
- [ ] **Step 2: Run to verify failure** (from `janeway/src`): `pytest ../../wjs-profile-project/.claude/worktrees/bugfix+issue-3333-unify-permanently-disabled-label/wjs/jcom_profile/tests/test_staff_parameters.py -k permanently_disabled -v`. Expected: FAIL with `AttributeError: ... is_permanently_disabled_as_editor`. If the editable install points at the main checkout and not the worktree, run `pip install -e .` from the worktree first, or the run will not see the change.
- [ ] **Step 3: Implement** `JCOMProfile.is_permanently_disabled_as_editor` as `return not self.is_enabled_as_editor(journal) or self.is_disabled_by_eo(journal)`, with a docstring noting it is the single condition behind the "Permanently disabled" label. Then add the `@register.filter` wrapper in `templatetags/wjs_review.py` that delegates to `user.jcomprofile`.
- [ ] **Step 4: Run to verify pass**: same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(jcom_profile): add unified is_permanently_disabled_as_editor predicate`

### Task 2: Use the unified label in both editor lists

**Files:**
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html:12-13`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/journal_editors/elements/editor_parameters_list.html:10`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/select_reviewer/elements/reviewers_list.html:19`
- Modify: `wjs/plugins/wjs_review/views.py:3669` (docstring wording only: "self disabled" → "permanently disabled")
- Test: `wjs/plugins/wjs_review/tests/test_views.py` (assign editor, next to the keyword-score test at ~1625); `wjs/plugins/wjs_review/tests/test_views_base.py` (journal editors, after `test_wjs_journal_editors_title_and_sticky_header`)

**Interfaces:**
- Consumes: filter `is_permanently_disabled_as_editor` from Task 1 (both templates already load `wjs_review`)

- [ ] **Step 1: Write the failing tests.**
  - `test_assign_editor_shows_unified_label_once` in `test_views.py`: model it on the keyword-score test (`submitted_workflow`, `eo_user`, `create_jcom_user`, `review_settings`). Create four section editors, each with a `StaffWorkloadParameters` row, with `(enabled, brake_on)` of `(False, 0)`, `(True, -1)`, `(False, -1)` and `(True, 0)`, and use distinct last names. GET `wjs_assign_editor`. Assert `"Permanently disabled"` appears exactly 3 times (`content.count`), and that `"Permanently self disabled"` and `"Disabled by EO"` are absent.
  - `test_select_reviewer_shows_unified_label`: find the existing select-reviewer view test (`grep -n "wjs_select_reviewer" wjs/plugins/wjs_review/tests/`) and reuse its setup. Make one reviewer unavailable via `is_available_as_reviewer` false (no vacancy, not an author). Assert `"Permanently disabled"` is in the content and `"Permanently self disabled"` is not.
  - `test_journal_editors_shows_unified_label_for_eo_disabled` in `test_views_base.py`: one section editor on `journal` with `enabled=True, brake_on=-1`. GET `wjs_journal_editors` as `eo_user`. Assert `"Permanently disabled"` is in the content and `"Permanently self disabled"` is not. Add a second case, with an enabled editor and `brake_on=0`, asserting the label is absent.
- [ ] **Step 2: Run to verify failure**: `pytest <worktree>/wjs/plugins/wjs_review/tests/test_views.py -k unified_label` and `... test_views_base.py -k unified_label`. Expected: FAIL (old text rendered, and no label for the EO-disabled editor on the Editor Management page).
- [ ] **Step 3: Implement.**
  - `editor_list.html`: replace lines 12-13 with one line: `{% if user|is_permanently_disabled_as_editor:journal %} - <span class="text-danger">{% translate "Permanently disabled" %}</span>{% endif %}`
  - `editor_parameters_list.html`: replace line 10 with `{% if editor|is_permanently_disabled_as_editor:journal %}<br><span class="text-danger">{% translate "Permanently disabled" %}</span>{% endif %}`
  - `reviewers_list.html`: change only the msgid on line 19 to `{% translate "Permanently disabled" %}`; keep the `elif` condition.
  - Update the `views.py` docstring wording.
- [ ] **Step 4: Run to verify pass**: the two commands above, then the whole of `test_views_base.py` and the `wjs_assign_editor` tests in `test_views.py` for regressions. Expected: PASS.
- [ ] **Step 5: Verify no leftovers**: `grep -rnE "Permanently self disabled|Disabled by EO" wjs --include=*.html --include=*.py`. Expected: no matches.
- [ ] **Step 6: Run `pre-commit run --all-files`**. Expected: all hooks pass.
- [ ] **Step 7: Commit** `fix(wjs_review): show "Permanently disabled" for self- and EO-disabled editors`, with `Refs wjs/specs#3333` in the footer.

## Decisions

- Reviewers' "Permanently self disabled" (`reviewers_list.html:19`) is reworded to the same "Permanently disabled" label (confirmed by the user), so the old msgid disappears from the codebase.
