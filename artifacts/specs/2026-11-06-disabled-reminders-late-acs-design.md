# Late attention conditions must fire even when the last reminder was disabled

- **Issue:** wjs/specs#2215 ("Alerts not triggered for delayed paper (EO disabled automatic reminder)")
- **Related:** wjs/specs#2823 (same problem, closed without a fix); AC specifications, "New Issue 10" (ref. #2464)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-11-06

## Intent

On JCOM_1562 a reviewer was pending for months, but no alert (attention condition, AC) ever reached the EO: the EO
had manually disabled the last automatic reminder of the sequence, and the paper dropped out of sight.

The "late" ACs for Editor, EO and Director use the *last reminder of a sequence* as a proxy for "enough time has
elapsed", and they only look at `Reminder.date_sent`. A reminder disabled before being sent keeps
`date_sent = NULL` forever, so the AC can never fire.

Disabling a reminder must stop the **notification** (the email to editor / reviewer / author), but must **not**
stop the related ACs: they must fire on the same schedule they would have followed had the reminder been sent.

## Current state

`Reminder` (`wjs/plugins/wjs_review/models.py`) has `date_due` (DateField, the day the reminder is scheduled to be
sent by the daily job), `date_sent` (DateTimeField, nullable) and `disabled` (BooleanField).

The affected condition functions in `wjs/plugins/wjs_review/conditions.py`, all of which filter only on
`date_sent`:

| Function | Watched reminder code(s) | Current threshold |
|---|---|---|
| `reviewer_is_late(article, for_editor)` | `REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3`, `REVIEWER_SHOULD_WRITE_REVIEW_2` | Editor: `date_sent__lte=now`; EO/Director: `date_sent__lte=now - 5 days` |
| `editor_is_late` | `EDITOR_SHOULD_MAKE_DECISION_3` | `date_sent__date__lt=today - 3 days` |
| `needs_assignment_all_editorreminders_sent` | `EDITOR_SHOULD_SELECT_REVIEWER_3` | `date_sent__date__lte=now - 5 days` |
| `any_reviewer_is_late_after_reminder` (Editor: "Reviewer does not respond") | `REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_3`, `REVIEWER_SHOULD_WRITE_REVIEW_2` | `date_sent__date__lt=today - WJS_REMINDER_LATE_AFTER` |
| `author_revision_is_late_all_reminders_sent(article, late_after_days)` | `AUTHOR_SHOULD_SUBMIT_MAJOR_REVISION_2` / `..._MINOR_REVISION_2` | `date_sent__date__lt=today - late_after_days` (Editor 1, EO 2) |
| `author_technicalrevision_is_late_all_reminders_sent(article, late_after_days)` | `AUTHOR_SHOULD_SUBMIT_TECHNICAL_REVISION_2` | `date_sent__date__lt=today - late_after_days` (Editor 1, EO 2) |

`ToggleDisableRemindersForm.save` (`forms.py`) already re-evaluates time-based ACs after toggling; its comment says
this hook becomes effective once conditions are due-date-aware for disabled reminders.

## Goals

- Every function in the table above treats a reminder as "counting" if **either**:
  - it was sent before the cut-off (unchanged behaviour), **or**
  - it is `disabled=True` and its `date_due` is before the **same** cut-off.
- Behaviour is unchanged for enabled reminders (sent or not yet sent) and for every other condition.
- Each changed condition is covered by tests.

## Non-goals / out of scope

- Decoupling ACs from the reminder system entirely (time-elapsed-only ACs, spec "New Issue 10").
- Any change to how reminders are sent, scheduled, disabled or re-enabled.
- `clemency_days` (only affects already-sent reminders when due dates move).
- The production impact check ("are other papers in the same condition?") from the issue's open questions.
- The wjapp importer typo `reminder.disable = True` (instead of `disabled`) in
  `wjs/jcom_profile/import_wjapp/import_logic.py`: it means imported reminders are never disabled. Separate ticket
  if wanted.

## Anti-goals

- An enabled, not-yet-sent reminder whose due date has passed must **not** start counting (that is a delayed send,
  not a disabled one — it will be sent and then count normally).
- A disabled reminder whose `date_due` is not yet past the threshold must not fire the AC early.

## Decisions

Decided with the user:

- Scope is the six functions in the table, including `any_reviewer_is_late_after_reminder` (not listed in the issue,
  same defect).
- Fix as proposed in #2823 and in the issue: `Q(date_sent… < cut_off) | Q(disabled=True, date_due < cut_off)`.
- Documentation language for this flow: English.
- **Re-enabling an overdue disabled reminder clears the AC** until the new grace period elapses. Once re-enabled,
  the reminder is "enabled, unsent" and does not count. The daily job sends it, and the AC fires again after the
  usual threshold from the new `date_sent`. This is accepted: re-enabling is a deliberate "nudge again" action. No
  new field or migration. This behaviour is pinned by a test.

My calls (flagged for review):

- **`date_due` stands in for `date_sent`** for disabled reminders, because the daily job sends reminders on their
  `date_due`. Threshold mapping per function (same cut-off, translated to a date comparison on `date_due`):
  - `reviewer_is_late`, Editor (`date_sent__lte=now`): disabled branch is `date_due__lte=today`.
  - `reviewer_is_late`, EO/Director (`date_sent__lte=now - 5 days`): disabled branch is
    `date_due__lte=(now - 5 days).date()` (local date).
  - `needs_assignment_all_editorreminders_sent` (`date_sent__date__lte=now - 5 days`): disabled branch is
    `date_due__lte=` the same date.
  - The `__lt=cut_off_date` functions: disabled branch is `date_due__lt=cut_off_date`.
  The existing `date_sent` branch of each function is left exactly as it is.
- **One shared helper** builds the "sent, or disabled and overdue" condition, instead of duplicating the `Q` in six
  places. It must work both on `Reminder.objects` and on the related querysets returned by
  `assignment.all_reminders()`. A small module-level function in `conditions.py` (or a method on `Reminder`)
  returning a `Q` is enough; the exact shape is left to the builder.
- **`ToggleDisableRemindersForm.save` comment** is updated: the toggle now does change ACs. No code change there.
- **Post-deploy action:** run `rebuild_attention_conditions` so already-materialised ACs are re-evaluated. Listed
  in the MR's required actions.

## Tests

TDD, in the existing test modules (`wjs/plugins/wjs_review/tests/test_attentionconditions.py` and/or
`test_reminders.py`, following their fixtures). For each of the six functions (and both Editor and EO thresholds
where the function has them):

1. **Disabled and past threshold → fires.** Last reminder `disabled=True`, `date_sent=None`, `date_due` past the
   cut-off, task still late → the AC message is returned. (Fails before the fix.)
2. **Disabled but not past threshold → does not fire.** Same, but `date_due` inside the threshold.
3. **Regression: sent → fires** as before.
4. **Enabled, unsent, overdue → does not fire** (anti-goal).

One test pins the re-enable behaviour: a disabled, past-threshold reminder fires the AC, and after re-enabling it
(still unsent) the condition no longer fires.

If practical, one test checks that `ToggleDisableRemindersForm.save` now materialises the AC when a past-due
reminder is disabled.

## Left to the builder

- Exact name and location of the helper.
- Test fixture setup and parametrisation.
- Whether to add a `__date` lookup or compare against a pre-computed local date, as long as the timezone handling
  matches the existing `date_sent` branch.
