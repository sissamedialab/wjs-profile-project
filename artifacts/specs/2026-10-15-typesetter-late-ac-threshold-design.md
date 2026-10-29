# Typesetter-late attention condition: start from last action + 4 days

- **Issue:** wjs/specs#3264 — "Attention Condition shown for typ even when not late"
- **Stakeholder spec:** wjs/specs#3264, note 78773
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-10-15
- **Status:** approved design

## Problem

The `typesetter_late` attention condition (AC) is shown to the typesetter and
to the EO even when the typesetter is not late yet, with the message
"Typesetter is 0 days late" / "You are 0 days late".

The typesetting assignment's `due` date is always set at creation time to
`now + TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` (3 days) — in the three places
that create a `TypesettingAssignment` in `logic__production.py` — and no form
lets anyone edit it afterwards. `conditions.is_typesetter_late()` fires when
`today >= assignment.due`, so the AC appears on the due date itself, i.e. on
*last action + 3 days*, with a lateness of 0 days.

## Requirements

From the issue and the stakeholder's note 78773:

1. The AC must start on *date of the last action + 4 days*: if the previous
   action took place on 10.10, the AC starts on 14.10.
2. The AC is shown only when the typesetter is at least one day late (never
   "0 days late").
3. (Decided during design) The message must use the correct singular/plural
   form: "1 day late", "N days late".

`TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` (3) is correct and stays unchanged:
the stakeholder's "+ 4 days" means "more than
`TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` days after the last action". Verified
that the setting is the only source of `due`: it is defined once
(`wjs/defaults/settings.py`), never overridden, used by all three
`TypesettingAssignment.objects.create()` calls (`logic__production.py`), and
`due` is never modified afterwards (no edit form; the wjapp import commands
only delete assignments). Hence `due = last action +
TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS`, and requirement 1 is exactly
`today > due`, which also satisfies requirement 2.

## Design

All changes are in `conditions.is_typesetter_late()`:

- **Threshold:** change the check from `today >= assignment.due` to
  `today > assignment.due`. On the first day the AC is shown, the lateness is
  1 day.
- **Today's date:** compute it with `timezone.localdate()` instead of
  `timezone.now().date()`. `due` is a `DateField` populated from an aware
  datetime, which Django converts to the *local* date; comparing it with the
  UTC date could be off by one day around midnight.
- **Pluralization:** pick "day"/"days" from the number of days, so the
  wording reads "1 day late" / "N days late", for both variants (a plain
  conditional: like the other messages in `conditions.py`, these strings are
  not translated):
  - EO: "Typesetter is 1 day late" / "Typesetter is N days late"
  - typesetter (`for_typesetter=True`): "You are 1 day late" / "You are N days late"

No changes to `ac_service._evaluate_typesetter_late()`: it already upserts the
AC when the condition returns a message and resolves it otherwise.

### Existing data

No data migration. AC rows that currently read "… 0 days late" are resolved by
the evaluator on the next rebuild (nightly `rebuild_attention_conditions`, or
any event that re-evaluates the article), because the condition now returns
`""` on the due date.

## Author proofing (added after stakeholder confirmation)

The stakeholder confirmed (2026-10-15) that the same rule applies to the
`author_proofing_late` AC (`conditions.is_author_proofing_late()`, shown to
the EO): "Proofing is late by 0 days" must not appear on the due date.

Verified that the fix is required:

- `GalleyProofing.due` is a `DateTimeField`. Its only writer
  (`logic__production.RequestProofs._create_proofing_assignment`) assigns a
  *date* (`today + PROOFING_ASSIGNMENT_MAX_DUE_DAYS` (7) on round 1,
  `+ PROOFING_ASSIGNMENT_MIN_DUE_DAYS` (3) afterwards), which Django stores as
  local midnight of the due day.
- `timezone.now() >= assignment.due` is therefore true from 00:00 of the due
  day, with `(now - due).days == 0` → "Proofing is late by 0 days".
- "Was expected by" formats the DB value, which is in UTC (22:00/23:00 of the
  previous day): it shows the day *before* the due date.

Design, consistent with the typesetter fix:

- compare local dates: `timezone.localdate() > timezone.localtime(due).date()`;
- "Was expected by" shows the local due date;
- pluralization: "Proofing is late by 1 day." / "… by N days.";
- a small shared helper formats "1 day" / "N days" for both conditions.

No changes to `ac_service._evaluate_author_proofing_late()` and no data
migration: stale "0 days" rows are resolved on the next rebuild.

Also (decided after the final review): `RequestProofs._create_proofing_assignment`
computes `due` from `timezone.now().date()`, the UTC date. A request made
between local 00:00 and 01:00/02:00 gets a due date one day earlier than
"local today + N". It now uses `timezone.localdate()`; existing assignments are
not touched.

## Out of scope

- The `TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` / `PROOFING_ASSIGNMENT_*_DUE_DAYS`
  settings are unchanged; the typesetting `due` computation is unchanged (an
  aware datetime saved into a `DateField` already becomes the local date).
- The evaluators still loop over all typesetting/proofing assignments of the
  article, including completed ones from earlier rounds (pre-existing).

## Testing

New tests (TDD) in a new focused module
`wjs/plugins/wjs_review/tests/test_attentionconditions_typesetter.py` (as for
`test_attentionconditions_accepted.py` / `_rfp.py`), freezing time relative to
an assignment's `due`:

- Unit tests on `conditions.is_typesetter_late()`:
  - `due` in the future → `""`;
  - today == `due` (last action + 3 days) → `""`;
  - today == `due + 1` (last action + 4 days) → "Typesetter is 1 day late" and,
    with `for_typesetter=True`, "You are 1 day late";
  - today == `due + 3` → "Typesetter is 3 days late" / "You are 3 days late";
  - assignment without `due` → `""`;
  - just after local midnight of `due + 1`, while the UTC date is still `due`
    → late by 1 day (pins `timezone.localdate()`).
- Author proofing, in `wjs/plugins/wjs_review/tests/test_attentionconditions_proofing.py`,
  with `due` stored as in production (local midnight, read back in UTC):
  - on the due date → `""`;
  - just after local midnight of `due + 1` → "Proofing is late by 1 day. Was
    expected by <due>. Please contact the author.";
  - `due + 3` → "… late by 3 days …"; no assignment / no `due` → `""`;
  - evaluator: no `author_proofing_late` AC for the EO on the due date, the AC
    the day after.
- `RequestProofs` run just after local midnight (UTC still on the previous
  day): `due` is local today + `PROOFING_ASSIGNMENT_MAX_DUE_DAYS`.
- The existing `test_author_proofing_is_late` keeps passing (its expected
  "Was expected by" date is computed in local time).
- Evaluator test through the AC service: no `typesetter_late` AC for the
  typesetter and the EO on the due date; the AC exists the day after; an AC
  existing from the old behaviour is resolved when re-evaluated on the due
  date.
