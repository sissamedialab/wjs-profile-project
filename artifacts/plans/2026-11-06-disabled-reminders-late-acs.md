# Late ACs fire for disabled last reminders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The six "late" attention-condition functions in `conditions.py` also count a *disabled* last reminder
whose `date_due` is past the same cut-off they already apply to `date_sent`.

**Architecture:** One `Q`-builder helper in `wjs/plugins/wjs_review/conditions.py` combines "sent before cut-off"
with "disabled and due before cut-off". Each affected function swaps its `date_sent…` filter kwarg for the helper.
The `date_sent` branch of every function stays byte-for-byte identical. No model, migration or form logic change.

**Tech Stack:** Django ORM, pytest / pytest-django, freezegun (already used by the suite).

**Spec:** `artifacts/specs/2026-11-06-disabled-reminders-late-acs-design.md`

## Global Constraints

- Issue: wjs/specs#2215. Branch: `bugfix/issue-2215-late-acs-disabled-reminders`. Docs language: English.
- Scope is exactly these functions: `reviewer_is_late`, `editor_is_late`, `needs_assignment_all_editorreminders_sent`,
  `any_reviewer_is_late_after_reminder`, `author_revision_is_late_all_reminders_sent`,
  `author_technicalrevision_is_late_all_reminders_sent`.
- An **enabled**, unsent, overdue reminder never counts. A disabled reminder whose `date_due` is not past the
  threshold never counts.
- Re-enabling an overdue disabled reminder makes the condition stop firing (accepted behaviour, pinned by a test).
- No new field, no migration, no change to sending/disabling reminders.
- Tests run from the Janeway checkout (`/home/andrea/Devel/sissa/janeway/src`), see `.claude/rules/tests.md`:
  `pytest --create-db ../../wjs-profile-project/<path>::<test>`. Never run the full suite per task (10+ min).

## Review Focus

1. **Timezone boundary on the EO 5-day threshold of `reviewer_is_late`**: `date_sent__lte=now-5d` is a datetime
   compare, the disabled branch compares a `DateField`. A reminder due exactly on `localtime(now-5d).date()` must
   count (`__lte`); one due a day later must not. Pinned in Task 2.
2. **Mixed sequence**: earlier reminders sent, last one disabled — the condition looks only at the watched (last)
   code, so it must fire on the disabled last reminder alone. Covered implicitly by every "disabled → fires" test
   (fixtures leave earlier reminders as they are).
3. **Disabled reminder on a completed/declined task**: conditions already filter the target (pending assignments,
   `date_completed__isnull=True`); a disabled overdue reminder on a completed revision request must not fire.
   Pinned in Task 3.
4. **Re-enabling**: disabled+overdue fires, then re-enabled (still unsent) does not. Pinned in Task 4.
5. **Toggle hook materialises the AC**: toggling `disabled` through `ToggleDisableRemindersForm` creates the stored
   `AttentionCondition` for the EO without waiting for the nightly job. Pinned in Task 4.

---

### Task 1: Helper + editor-side conditions (`editor_is_late`, `needs_assignment_all_editorreminders_sent`)

**Files:**
- Modify: `wjs/plugins/wjs_review/conditions.py` (helper near the top, after `Account = get_user_model()`;
  `editor_is_late` ~L80; `needs_assignment_all_editorreminders_sent` ~L198)
- Create: `wjs/plugins/wjs_review/tests/test_late_acs_disabled_reminders.py`

**Interfaces:**
- Produces:
  ```python
  def sent_or_disabled_and_due(sent: Q, due: Q) -> Q:
      """Match reminders sent per ``sent``, or disabled and due per ``due`` (never-sent disabled reminders)."""
  ```
  Returns `sent | (Q(disabled=True) & due)`. Callers pass the original `date_sent…` lookup as `sent` and the
  equivalent `date_due…` lookup as `due`.
- Produces (test module helper, reused by Tasks 2–4):
  ```python
  def _set_reminder(target, code: Reminder.ReminderCodes, *, date_due: datetime.date,
                    disabled: bool = False, date_sent: datetime.datetime | None = None) -> Reminder:
  ```
  Fetches the existing reminder with `code` on `target` (via `content_type`/`object_id`) and updates the three
  fields. If the workflow did not create one, create it copying `recipient`, `actor`, `message_subject`,
  `message_body` from any other reminder of the same target.

- [ ] **Step 1: Write failing tests** (all `@pytest.mark.django_db`, call the condition functions directly):
  - `test_editor_is_late_fires_for_disabled_last_reminder`: `submitted_review_assignment` fixture (all reviews done,
    EDMD reminders exist); `EDITOR_SHOULD_MAKE_DECISION_3` disabled, unsent, `date_due = today - 4 days`
    → `conditions.editor_is_late(article) == "Editor has not yet taken a decision"`.
  - `test_editor_is_late_ignores_disabled_reminder_within_threshold`: same, `date_due = today - 3 days` → `""`.
  - `test_editor_is_late_ignores_enabled_unsent_overdue_reminder`: `disabled=False`, `date_due = today - 10` → `""`.
  - `test_needs_assignment_eo_fires_for_disabled_last_reminder`: `assigned_article` (no review assignments);
    `EDITOR_SHOULD_SELECT_REVIEWER_3` disabled, unsent, `date_due = (now - 5 days).date()`
    → `"Review process not yet started/restarted"`.
  - `test_needs_assignment_eo_ignores_disabled_reminder_within_threshold`: `date_due = (now - 4 days).date()` → `""`.
  - `test_needs_assignment_eo_ignores_enabled_unsent_overdue_reminder` → `""`.
  ("today" = `timezone.localtime(timezone.now()).date()` throughout.)

- [ ] **Step 2: Run, expect the two "fires" tests to FAIL** (return `""`), the others to pass.
  `pytest --create-db ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_late_acs_disabled_reminders.py -v`

- [ ] **Step 3: Implement** `sent_or_disabled_and_due` and use it in the two functions:
  - `editor_is_late`: `sent=Q(date_sent__date__lt=cut_off_date)`, `due=Q(date_due__lt=cut_off_date)`.
  - `needs_assignment_all_editorreminders_sent`: compute `cut_off = timezone.now() - timedelta(days=5)` once;
    `sent=Q(date_sent__date__lte=cut_off)` (unchanged), `due=Q(date_due__lte=timezone.localtime(cut_off).date())`.
  Update both docstrings ("…has been sent, or disabled with its due date, at least N days ago").

- [ ] **Step 4: Run the new module → PASS; run regression** `test_attentionconditions.py::test_needs_assignment`
  and `::test_editor_is_late` → PASS.

- [ ] **Step 5: Commit** (only if per-change commits were chosen) `fix(wjs_review): count disabled editor reminders in late ACs`

### Task 2: Reviewer conditions (`reviewer_is_late`, `any_reviewer_is_late_after_reminder`)

**Files:**
- Modify: `wjs/plugins/wjs_review/conditions.py` (`reviewer_is_late` ~L44, `any_reviewer_is_late_after_reminder` ~L336)
- Test: `wjs/plugins/wjs_review/tests/test_late_acs_disabled_reminders.py`

**Interfaces:**
- Consumes: `sent_or_disabled_and_due` (Task 1), `_set_reminder` (Task 1).

- [ ] **Step 1: Write failing tests**, using the `review_assignment` fixture with `date_due` set to `today - 1`
  (pending, overdue). Parametrize over the two watched codes
  (`REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3` → `"Reviewer has not yet answered the invitation"`,
  `REVIEWER_SHOULD_WRITE_REVIEW_2` → `"Reviewer is late"`; create the REWR reminder via `_set_reminder` if absent):
  - `test_reviewer_is_late_editor_fires_for_disabled_last_reminder`: disabled, unsent, `date_due = today`
    → `conditions.reviewer_is_late(article, for_editor=True) == <message>`.
  - `test_reviewer_is_late_editor_ignores_disabled_future_reminder`: `date_due = today + 1` → `""`.
  - `test_reviewer_is_late_eo_fires_on_threshold_day`: `date_due = localtime(now - 5 days).date()`
    → `reviewer_is_late(article) == <message>` (Review Focus 1).
  - `test_reviewer_is_late_eo_ignores_day_after_threshold`: `date_due = localtime(now - 4 days).date()` → `""`.
  - `test_reviewer_is_late_ignores_enabled_unsent_overdue_reminder`: `disabled=False`, `date_due = today - 10`,
    both `for_editor` values → `""`.
  - `test_any_reviewer_late_after_reminder_fires_for_disabled`: disabled, unsent,
    `date_due = today - (settings.WJS_REMINDER_LATE_AFTER + 1)` → `"Reviewer does not respond. Please take action"`.
  - `test_any_reviewer_late_after_reminder_ignores_disabled_within_threshold`:
    `date_due = today - settings.WJS_REMINDER_LATE_AFTER` → `""`.

- [ ] **Step 2: Run, expect the "fires" tests to FAIL.**

- [ ] **Step 3: Implement.**
  - `reviewer_is_late`: replace `.filter(code=code, date_sent__lte=time_threshold)` with
    `.filter(sent_or_disabled_and_due(Q(date_sent__lte=time_threshold),
    Q(date_due__lte=timezone.localtime(time_threshold).date())), code=code)`.
  - `any_reviewer_is_late_after_reminder`: `sent=Q(date_sent__date__lt=cut_off_date)`,
    `due=Q(date_due__lt=cut_off_date)`.
  Update both docstrings.

- [ ] **Step 4: Run new tests → PASS; regression** `test_attentionconditions.py::test_reviewer_is_late`,
  `test_reminders.py -k test_any_reviewer_is_late_after_reminder`, and
  `test_attentionconditions.py -k postpone` → PASS.

- [ ] **Step 5: Commit** (if per-change) `fix(wjs_review): count disabled reviewer reminders in late ACs`

### Task 3: Author revision conditions

**Files:**
- Modify: `wjs/plugins/wjs_review/conditions.py` (`author_revision_is_late_all_reminders_sent` ~L390,
  `author_technicalrevision_is_late_all_reminders_sent` ~L444)
- Test: `wjs/plugins/wjs_review/tests/test_late_acs_disabled_reminders.py`

**Interfaces:**
- Consumes: `sent_or_disabled_and_due`, `_set_reminder`.

- [ ] **Step 1: Write failing tests.** Create the revision request with `HandleDecision` as in
  `test_attentionconditions.py::test_author_revision_is_late` (`date_due` 5 days in the past). Parametrize decision
  over `MAJOR_REVISION` (code `AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2`), `MINOR_REVISION`
  (`..._MINOR_REVISION_2`), and for the technical function `TECHNICAL_REVISION` (`..._TECHNICAL_REVISION_2`).
  Parametrize `late_after_days` over `1` (Editor) and `2` (EO):
  - `test_author_revision_late_fires_for_disabled_last_reminder`: disabled, unsent,
    `date_due = today - (late_after_days + 1)` → `"Revision is 5 days late. Pls consider reminding author"`
    (technical: `"Author has not updated metadata"`).
  - `test_author_revision_late_ignores_disabled_within_threshold`: `date_due = today - late_after_days` → `""`.
  - `test_author_revision_late_ignores_enabled_unsent_overdue`: `disabled=False`, `date_due = today - 10` → `""`.
  - `test_author_revision_late_ignores_disabled_on_completed_request` (Review Focus 3): disabled, past threshold,
    `revision_request.date_completed = now()` → `""`.

- [ ] **Step 2: Run, expect the "fires" tests to FAIL.**

- [ ] **Step 3: Implement**: in both functions `sent=Q(date_sent__date__lt=cut_off_date)`,
  `due=Q(date_due__lt=cut_off_date)`. Update both docstrings.

- [ ] **Step 4: Run new tests → PASS; regression** `test_attentionconditions.py -k "author_revision_is_late or
  author_technicalrevision"` → PASS.

- [ ] **Step 5: Commit** (if per-change) `fix(wjs_review): count disabled author reminders in late ACs`

### Task 4: Toggle hook, re-enable behaviour, stale comment

**Files:**
- Modify: `wjs/plugins/wjs_review/forms.py` (`ToggleDisableRemindersForm.save` comment, ~L2175–2179)
- Test: `wjs/plugins/wjs_review/tests/test_late_acs_disabled_reminders.py`

**Interfaces:**
- Consumes: `ToggleDisableRemindersForm(data={}, instance=reminder)` — `save()` flips `disabled` and runs
  `ACStateEvaluator(...).evaluate_time_based()`; `attention_condition_ignoring_unread` from `.test_helpers`.

- [ ] **Step 1: Write tests** (on the `review_assignment` setup of Task 2 — not yet accepted, `date_due = today - 1`
  — using `REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3`, unsent, enabled, `date_due = today - 10`; expected message
  `"Reviewer has not yet answered the invitation"`):
  - `test_toggle_disable_materialises_eo_ac`: before toggling the EO has no reviewer AC; toggle via the form →
    `attention_condition_ignoring_unread(article, eo)` returns the reviewer message.
  - `test_reenable_overdue_reminder_clears_ac`: toggle again (re-enable, still unsent) →
    `conditions.reviewer_is_late(article) == ""` and the EO AC is gone after the hook.

- [ ] **Step 2: Run** — both should PASS already thanks to Tasks 1–3 (they lock the behaviour in; if they fail,
  stop and report, do not change the form logic).

- [ ] **Step 3: Rewrite the comment** in `ToggleDisableRemindersForm.save`: condition functions now count disabled
  reminders whose due date is past the threshold (wjs/specs#2215), so toggling can create or resolve time-based
  ACs immediately; re-enabling an overdue unsent reminder resolves them until it is sent.

- [ ] **Step 4: Run the whole new module plus** `test_attentionconditions.py` and `test_reminders.py` → PASS.

- [ ] **Step 5: Commit** (if per-change) `test(wjs_review): pin reminder toggle effect on late ACs`

---

## Post-implementation (wrap-up, not a task)

- MR *Required actions*: run `rebuild_attention_conditions` after deploy.
- Towncrier fragment per `.claude/rules/workflow.md`, if the project uses it.
