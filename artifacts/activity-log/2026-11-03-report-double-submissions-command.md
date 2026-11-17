## 2026-11-03 — Race tests and repair command for step-8 double submissions
**What:** Added race tests that drive the real step-8 view from two threads/DB connections, and the `report_double_submissions` management command: without arguments it lists the articles damaged by double submissions with the problem, evidence and proposed fix; `--repair ID [ID ...]` fixes only the given articles.
**Why:** specs#3233: the fix (article row lock in wjs-submission's step-8 view) stops new cases, but past overlapping submissions left articles with an overwritten `date_submitted` or a stage inconsistent with the workflow. On the 2026-11-03 production dump the report finds exactly 3836 (JCOM, published) and 4351 (JCOMAL).
**Decisions:** No data migration and no unattended fix (user's call): the EO reads the report, then runs `--repair` on chosen ids. Detection uses DB data only (revision request dates, stage log, workflow state), never message subjects, which are per-journal settings. The original submission date comes from the first `ArticleStageLog` out of "Unsubmitted"; without it the problem is reported as manual. Repairs use `QuerySet.update` so no events, transitions or stage log entries are fired; each article is re-checked and repaired in its own transaction. Published articles get a warning to check public metadata by hand. No prose docs page: the command's `--help` and module docstring are the documentation (user's choice at doc-sync).
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming (builder check subagent) | ≈57k | ≈1m |
| Implementation | superpowers:executing-plans (inline) | — | — |
| Review | superpowers:requesting-code-review (final reviewer, opus) | ≈110k | ≈3m |

**Considered & dropped:** Searching production logs for the 500s (log access not guaranteed, and silent re-submissions leave no error); matching acknowledgement subjects in the message table (subjects vary by journal setting).
**Follow-ups:** Bump `wjs-submission` minimum version in setup.cfg once the wjs-submission fix is released (plan Task 6). Deferred minor M2: `--repair` re-detection is not under a row lock, and an unexpected exception on one id stops the remaining ids. Run the report on production and repair 3836/4351 with EO agreement; 3836 is published, so its public metadata needs a manual check. Review-fixed: an open revision no longer hides re-submitted articles; articles without a workflow no longer crash the report.
**Refs:** wjs/specs#3233; spec artifacts/specs/2026-11-03-step8-double-submit-design.md; plan artifacts/plans/2026-11-03-step8-double-submit.md; Eval: 76% — artifacts/evaluations/2026-11-03-step8-double-submit.md
