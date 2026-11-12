## 2026-11-06 — Late ACs fire even when the last reminder was disabled
**What:** The six "late" attention conditions for Editor/EO/Director now count a disabled, never-sent last reminder
from its `date_due`, with the same threshold they apply to `date_sent`, via the shared helper
`sent_or_disabled_and_due` in `conditions.py`.
**Why:** On JCOM_1562 the EO disabled the last reminder to the Editor; `date_sent` stayed NULL, so no EO alert ever
fired and the paper was lost for months (wjs/specs#2215, same as #2823). Disabling must stop the email, not the AC.
**Decisions:**
- Scope extended (with the user) to `any_reviewer_is_late_after_reminder` (Editor "Reviewer does not respond"),
  not listed in the issue but with the same defect.
- `date_due` stands in for the send date because the daily job sends on `date_due`; enabled-but-unsent overdue
  reminders still do not count (they are a delayed send).
- Re-enabling an overdue disabled reminder clears the AC until the reminder is sent and the usual grace period
  elapses — accepted by the user as a deliberate "nudge again"; pinned by a test. No new field or migration.
- The toggle form's AC re-evaluation hook now actually changes ACs; its comment was rewritten.
- e2e: skipped (backend-only; Playwright mode "skip").
- Eval: 85% — artifacts/evaluations/2026-11-06-disabled-reminders-late-acs.md
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | general-purpose (brainstorming builder check) | ≈74k | ≈1m |
| Review | general-purpose, opus (final whole-branch review) | ≈90k | ≈1.5m |

**Considered & dropped:** keeping the AC after re-enable (option b: needs a "disabled after due" field + migration);
fully time-based ACs decoupled from reminders ("New Issue 10", out of scope).
**Follow-ups:**
- Run `rebuild_attention_conditions` after deploy; expect a burst of escalated ACs on old papers with disabled last
  reminders (intended).
- Production impact check (other papers in the same state) is still open on the issue.
- wjapp importer sets `reminder.disable = True` (typo for `disabled`) in `import_wjapp/import_logic.py`: imported
  reminders are never disabled — candidate separate ticket.
- Deferred test minors: mixed sequence (earlier sent, last disabled), completed review assignment with disabled
  last reminder.
- Local env gotchas: run pytest from `janeway/src` with `-p no:freezegun`, `PYTHONPATH=<repo>`, absolute test paths;
  `-n7 --create-db` hits Postgres "out of shared memory" (use `-n4`); `opentelemetry` missing locally breaks
  `test_tracing*`.
**Refs:** wjs/specs#2215, #2823; spec artifacts/specs/2026-11-06-disabled-reminders-late-acs-design.md; plan
artifacts/plans/2026-11-06-disabled-reminders-late-acs.md.
