# Drop the duplicate "Issues after acceptance" notification to the EO

- **Issue:** wjs/specs#3176 (from the JCAP go-live feedback wjs/specs#3162)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-10-13
- **Status:** approved design

## Problem

When a JCAP paper with the Transformative Agreement (TA) access mode is
accepted, the EO receives two notifications:

1. the TA-specific one (settings `jcap_ta_pending_subject` / `_body`), sent by
   the acceptance check `jcap_ta_not_yet_confirmed`
   (`events/checks_after_acceptance.py`) before it returns `False`;
2. the generic "Issues after acceptance - article N not ready for
   typesetters.", sent by `VerifyProductionRequirements._log_acceptance_issues`
   (`logic__production.py`) whenever any check returns `False`.

Both are `FULL`-verbosity messages to the EO. The second one is redundant and
must be dropped. QA still saw it on dev on 2026-10-07: MR !1543 (#3174) only
mentioned this issue, nothing fixed it.

## Requirements

- R1. When the acceptance checks hold a paper in `Accepted`,
  `VerifyProductionRequirements` sends no message of its own.
- R2. A JCAP TA paper held in `Accepted` produces exactly one EO notification
  from the acceptance checks: the TA-specific one.
- R3. Check functions that hold a paper are responsible for notifying the EO;
  the contract is documented where the checks are run.
- R4. No conflict with the open MR !1556 (#3131), which rewrites and renames
  `jcap_ta_not_yet_confirmed`.

## Design

### Code (R1, R3)

In `logic__production.py`, `VerifyProductionRequirements`:

- delete the method `_log_acceptance_issues`;
- in `run()`, delete its call and the comment that refers to "a message to
  EO"; keep the comment saying that no exception is raised, so that the editor
  can still accept the article;
- document in the `_perform_checks` docstring that a check function returning
  `False` holds the paper in `Accepted` and must notify the EO itself (as
  the JCAP check does), without naming the function, which !1556 renames;
  the EO also gets the
  `ACCESS_MODE_TO_CHECK` AC.

The `from django.urls import reverse_lazy` import becomes unused (its only
use is in `_log_acceptance_issues`) and is removed.

`events/checks_after_acceptance.py` is **not** touched (R4).

Today the only check that can hold a paper is the JCAP one: every other
journal uses `always_pass`. A future check that holds a paper without
notifying would leave the EO with the `ACCESS_MODE_TO_CHECK` AC only; the
documented contract covers that.

### Considered and dropped

Keeping the generic message as a fallback, sent only when no check function
notified the EO: it adds machinery for a case that does not exist today.

## Error handling

Unchanged: a held paper stays in `Accepted`, no exception is raised, the
`ACCESS_MODE_TO_CHECK` AC is still created in the same transaction.

## Testing (TDD)

New tests go in `tests/test_ready_for_typesetter_timeline.py` (which already
covers the Accepted → ReadyForTypesetter path), not in the test modules that
!1556 adds:

1. A paper held by `always_reject`: no message with subject starting
   "Issues after acceptance" exists for the article (fails today).
2. A JCAP TA paper: the paper is in `Accepted`, its messages are cleared,
   the access mode is set to TA, `VerifyProductionRequirements(workflow).run()`
   runs with the check functions configured for JCAP in the default settings
   (read from `settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]`, not
   a hard-coded function name, so the test survives the rename in !1556). The
   EO receives exactly one message, whose subject is the TA one (fails today:
   two messages).

In `tests/test_workflow.py`, `test_accepted_workflow_issues` drops its
`# TODO: check for a message to the EO after approach is validated`, now
covered by the tests above.

## Out of scope

- Any change to `jcap_ta_not_yet_confirmed` or to its settings (owned by
  !1556).
- Removing the `jcap_ta_pending_*` notification itself, or changing its text.
