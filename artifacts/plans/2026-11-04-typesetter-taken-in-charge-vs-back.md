# Typesetter "Taken in charge" vs "Back to typesetter" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Distinguish papers in `TypesetterSelected` as "Taken in charge" (round 1) or "Back to typesetter" (round > 1) with their own badge colours, and show the production version in the typesetter "Working on" list and the "Pending issues" page.

**Architecture:** Two new display-only `ReviewComputedStates`, returned by `ArticleWorkflow.state_value` from the latest `TypesettingRound`; every existing badge/label/API consumer picks them up unchanged. Badge colours are two new `color-state-*` classes in wjs-themes. Version display reuses the existing `versioned_number` template filter.

**Tech Stack:** Django, django-fsm, Janeway `typesetting.TypesettingRound`, pytest (from `janeway/src`), SCSS (wjs-themes `wjs-bootstrap`).

**Spec:** `artifacts/specs/2026-11-04-typesetter-taken-in-charge-vs-back-design.md`

## Global Constraints

- Documentation language: English (code, comments, commits too).
- Branch `feature/issue-3120-typesetter-round-labels` in both `wjs-profile-project` and `/home/yakky/Projects/sissa/wjs-themes`; read/write nothing outside these two repos (plus the scratchpad).
- Computed states are display-only: no change to `state`, FSM transitions, `states.py`, permissions, querysets, migrations.
- Values: `TAKEN_IN_CHARGE = "taken_in_charge"` / label `"Taken in charge"`; `BACK_TO_TYPESETTER = "back_to_typesetter"` / label `"Back to typesetter"`.
- Version format stays `<pk>/v<round>` (existing `versioned_number` filter).
- Do not touch `setup.cfg`'s `wjs-themes` pin; no `.po` changes.
- Python style: black 119, double quotes, docstrings imperative; templates 2-space indent; run `pre-commit run --all-files` in each repo before committing.
- Tests run from `/home/yakky/Projects/sissa/janeway/src` (Janeway checkout — running pytest there is the documented test workflow; do not edit files there). Run targeted tests only, not the full suite.

## Review Focus

1. Article in `TypesetterSelected` with **no** `TypesettingRound` (legacy/imported data) → must return "Taken in charge", not crash. Test in Task 1.
2. Rounds not starting at 1 (the G8 tests create a round 0 next to round 2) → latest round decides; round 2 → "Back to typesetter". Test in Task 1.
3. States other than `TypesetterSelected` that have rounds (`Proofreading`, `ReadyForPublication`) → keep their raw state value. Test in Task 1.
4. Review-phase computed states unchanged (regression of the early-return reordering). Test in Task 1.
5. `state_value` is a `cached_property`: tests must re-fetch the workflow (`ArticleWorkflow.objects.get(pk=...)`) after each transition rather than reuse an instance.

---

### Task 1: Computed states in `ArticleWorkflow.state_value`

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (`ReviewComputedStates` ≈ l.444, `state_value` ≈ l.559)
- Modify: `wjs/plugins/wjs_review/tests/conftest.py:536` (fixture bug, see Step 1)
- Create: `wjs/plugins/wjs_review/tests/test_typesetting_computed_states.py`

**Interfaces:**
- Produces: `ArticleWorkflow.ReviewComputedStates.TAKEN_IN_CHARGE` (`"taken_in_charge"`), `ArticleWorkflow.ReviewComputedStates.BACK_TO_TYPESETTER` (`"back_to_typesetter"`); `state_value`/`state_label` return them for `TypesetterSelected`.

- [ ] **Step 1: Fix the `assigned_to_typesetter_proofs_done_article` fixture**

`conftest.py:536` passes the `ready_for_typesetter_article` fixture *function* instead of the `stage_proofing_article` argument. Change it to `_assigned_to_typesetter_proofs_done_article(stage_proofing_article, fake_request)`. (Fixture is currently unused, so nothing else changes.)

- [ ] **Step 2: Write the failing tests** in `test_typesetting_computed_states.py` (all `@pytest.mark.django_db`; re-fetch the workflow with `ArticleWorkflow.objects.get(pk=article.articleworkflow.pk)` before each assertion):

- `test_first_round_is_taken_in_charge(assigned_to_typesetter_article)` → `state == TYPESETTER_SELECTED`, `state_value == ReviewComputedStates.TAKEN_IN_CHARGE`, `state_label == "Taken in charge"`.
- `test_author_corrections_are_back_to_typesetter(assigned_to_typesetter_proofs_done_article)` → latest round number is 2, `state_value == BACK_TO_TYPESETTER`, `state_label == "Back to typesetter"`.
- `test_eo_send_back_is_back_to_typesetter(stage_proofing_article, eo_user, client)` → follow `test_production.py::test_eo_sends_back_to_typesetter` (set state to `READY_FOR_PUBLICATION`, POST to `wjs_send_back_to_typ`), then `state_value == BACK_TO_TYPESETTER`.
- `test_no_typesetting_round_is_taken_in_charge(assigned_to_typesetter_article)` → delete all `TypesettingRound` of the article (`TypesettingRound.objects.filter(article=...).delete()`), workflow still `TYPESETTER_SELECTED`, `state_value == TAKEN_IN_CHARGE`.
- `test_latest_round_decides(assigned_to_typesetter_article)` → create `TypesettingRound(article=..., round_number=0)` and `round_number=2` (round 1 already exists) → `BACK_TO_TYPESETTER`.
- `test_proofreading_keeps_raw_state(stage_proofing_article)` → `state_value == ReviewStates.PROOFREADING`.
- `test_review_states_unchanged(assigned_article)` → `state_value == ReviewComputedStates.ASSIGNED_TO_EDITOR` (`assigned_article` fixture: editor selected, no reviews).

Each assertion gets a message, per `.claude/rules/tests.md`.

- [ ] **Step 3: Run, expect FAIL**

Run (from `janeway/src`): `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_typesetting_computed_states.py -v`
Expected: the `TAKEN_IN_CHARGE`/`BACK_TO_TYPESETTER` tests fail with `AttributeError` on `ReviewComputedStates`; the last two pass.

- [ ] **Step 4: Implement**

Add the two members to `ReviewComputedStates` (labels via `_()`). In `state_value`, before the `states_when_article_is_considered_working_on` early return, branch on `self.state == ReviewStates.TYPESETTER_SELECTED`: latest round = `TypesettingRound.objects.filter(article=self.article).first()` (model ordering is `-round_number`); `round_number > 1` → `BACK_TO_TYPESETTER`, else (incl. `None`) → `TAKEN_IN_CHARGE`. Return `.value` like the other branches. Update the `state_value` docstring to mention the typesetting branch. Use a module-level import of `TypesettingRound` if no circular import, else follow the file's existing local-import pattern.

- [ ] **Step 5: Run, expect PASS**

Same command. Also run `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py -n4` → all pass (no regressions from the new states on production lists).

- [ ] **Step 6: Commit** (only if per-change commits were chosen at flow step 4)

`feat(wjs_review): distinguish taken in charge and back to typesetter states` — files: `models.py`, `tests/conftest.py`, `tests/test_typesetting_computed_states.py`.

---

### Task 2: G8 API reflects the new labels

**Files:**
- Modify: `wjs/plugins/wjs_review/api/serializers.py` (`get_status` comment ≈ l.145)
- Modify: `wjs/plugins/wjs_review/tests/test_api.py` (≈ l.480-487 and `test_multiple_rounds_yield_a_single_row` ≈ l.499)

**Interfaces:**
- Consumes: Task 1's `ReviewComputedStates.TAKEN_IN_CHARGE` / `BACK_TO_TYPESETTER`.

- [ ] **Step 1: Update the tests** — in the W1 assertion replace the TODO block: expect `{"code": ReviewStates.TYPESETTER_SELECTED, "name": str(ReviewComputedStates.TAKEN_IN_CHARGE.label)}`. In `test_multiple_rounds_yield_a_single_row`, add an assertion that the single row's `status["name"] == str(ReviewComputedStates.BACK_TO_TYPESETTER.label)` while round 2 exists (check the paper is in `TYPESETTER_SELECTED` in that scenario; if not, skip this extra assertion and note why).

- [ ] **Step 2: Run** `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_api.py -v` → PASS (Task 1 already makes it pass; the point is pinning the behaviour). Also check the other tests in that file for hard-coded `"Typesetter selected"` names and update them the same way.

- [ ] **Step 3: Rewrite the `get_status` comment** to state the current behaviour (name is the computed label, e.g. "Taken in charge"/"Back to typesetter" while `code` stays `TypesetterSelected`), removing the "once specs#3120 adds" wording.

- [ ] **Step 4: Commit** (if per-change) — `test(wjs_review): pin typesetter computed labels in the G8 endpoint`.

---

### Task 3: Production version in "Working on" and "Pending issues"

**Files:**
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/lists/elements/typesetter/table_item.html:12`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/lists/elements/issue/table_article.html:17`
- Test: `wjs/plugins/wjs_review/tests/test_typesetting_computed_states.py` (same module as Task 1)

**Interfaces:**
- Consumes: existing filter `versioned_number(article) -> str` in `templatetags/wjs_articles.py` (already loaded by both templates).

- [ ] **Step 1: Write the failing tests**

- `test_typesetter_working_on_shows_version(assigned_to_typesetter_proofs_done_article, typesetter, client)` → `client.force_login(typesetter.janeway_account)`, GET `reverse("wjs_review_typesetter_workingon")`; response contains `f"{article.pk}/v2"` and the text `"Back to typesetter"`.
- `test_pending_issues_row_shows_version(assigned_to_typesetter_article, fake_request)` → `render_to_string("wjs_review/lists/elements/issue/table_article.html", {"article": article, "workflow": article.articleworkflow, ...}, request=fake_request)` contains `f"{article.pk}/v1"`. Add whatever extra context keys the template requires (read it); rendering the template directly avoids building a dated issue for `BaseWorkOnIssue.get_queryset`.

- [ ] **Step 2: Run, expect FAIL** (the ids render without `/vN`).

- [ ] **Step 3: Implement** — in both templates replace the link text `{{ article.pk }}` with `{{ article|versioned_number }}`. Keep the 2-space indentation.

- [ ] **Step 4: Run, expect PASS**, plus `djlint --profile=django` on both templates.

- [ ] **Step 5: Commit** (if per-change) — `feat(wjs_review): show production version in typesetter and pending issues lists`.

---

### Task 4: Badge colours in wjs-themes

**Files:**
- Modify: `/home/yakky/Projects/sissa/wjs-themes/wjs/themes/wjs-bootstrap/assets/sass/_badges_colors.scss`

**Interfaces:**
- Consumes: CSS class names `color-state-taken_in_charge`, `color-state-back_to_typesetter` (from `color-state-<state_value>` in `templatetags/wjs_articles.py:185`).

- [ ] **Step 1: Pick and verify colours** — with a small Python script in the scratchpad, compute WCAG contrast:
  - Taken in charge: start from `#fb7185` vs text `#1e2124`; must be ≥ 4.5:1 and visibly distinct from `#f05151` (Rejected) and `#f88a8a` (Not suitable).
  - Back to typesetter: start from `#991b1b` vs `#ffffff`; must be ≥ 4.5:1.
  - Adjust within the spec's hue direction if a check fails; record the final values and ratios for the MR.

- [ ] **Step 2: Edit the SCSS**
  - Header comment list: add `color-state-taken_in_charge` and `color-state-back_to_typesetter` after `color-state-TypesetterSelected`.
  - Variables, next to `$color-state-TypesetterSelected`: `$color-state-taken_in_charge` / `$hover-state-taken_in_charge: darken(..., 10%)`, same for `back_to_typesetter`.
  - Mixin blocks `&-taken_in_charge` and `&-back_to_typesetter`, same shape as `&-TypesetterSelected` (≈ l.447); `&-back_to_typesetter` also sets `color: #fff;`.
  - Keep `TypesetterSelected` untouched.

- [ ] **Step 3: Verify** — `pre-commit run --all-files` in wjs-themes (prettier on scss) passes. Compiling the CSS (`python manage.py build_assets` in `janeway/src`) writes into Janeway's static dir, outside the boundary: ask the user before running it; otherwise leave the visual check to the test environment.

- [ ] **Step 4: Commit** (if per-change, in wjs-themes) — `feat(badges): add taken in charge and back to typesetter state colours`.

---

### Task 5: Final check

- [ ] Run Task 1–3 test modules together plus `test_production.py` and `test_api.py`; all pass.
- [ ] `grep -rn "TYPESETTER_SELECTED" wjs --include=*.py | grep state_value` → no matches (no code compares `state_value` to the raw state).
- [ ] `pre-commit run --all-files` in both repos passes.
