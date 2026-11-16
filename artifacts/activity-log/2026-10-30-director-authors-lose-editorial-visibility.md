## 2026-10-30 — Directors who are authors lose editorial visibility on their own papers
**What:** A director who is a frozen author or the correspondence author of a paper no longer sees it on any director list, nor its reminders, reminder toggle, editor automatic-selection audit pages, audit list, Activity messages, or the editor column of the "Pending Issues" list.
**Why:** Issue wjs/specs#3013 (JCOMAL): directors saw the editor and review data of their own papers on the lists. !1516 had fixed Pending and Archived only, and "Search preprints" lost the exclusion because it overrides `_apply_base_filters`.
**Decisions:**
- One predicate, `ArticleWorkflowQuerySet.exclude_authored_by`, covering frozen and correspondence authors (the correspondence author is not always a frozen author). `communication_utils` and the audit list repeat the same two conditions, kept in sync by comments instead of a shared helper (spec choice, flagged by review).
- Template method in `DirectorPending`: the exclusion lives in `_apply_base_filters`, subclasses only override `_apply_state_filters`, so a later override can't drop it again.
- `has_eo_or_director_role_by_article` keeps the EO branch unconditional; only the director branch is author-aware. EO users who are also authors keep access (#2765 covers the EO case).
- `AutomaticAssignmentAuditArticle` checks the journal-level role first, then loads the workflow, so unauthorised users get 403 and not an existence-revealing 404; director-authors get 403 even with no audit rows.
- `ToggleDisableReminders` uses `Reminder.get_related_article()` and falls back to the journal-level check when there is no article or workflow.
- Whole-branch review found the "Pending Issues" list as a leak the spec had missed; fixed in this MR by hiding only the editor cell (`is_director_and_author` + `user_is_director_author`), since the row template is shared with the EO and typesetter lists.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming (builder-check subagent) | ≈72k | ≈1m |
| Implementation | superpowers:executing-plans (inline, no subagents) | not measured | ≈2h incl. ≈30m of suite runs |
| Review | superpowers:requesting-code-review (opus, whole branch) | ≈117k | ≈3m |

**Considered & dropped:**
- Problem 4 of the issue (missing article type and submission date in lists): dropped by the requester. Local data (`janeway_prod`, JCOM article 4524) shows it is an `IncompleteSubmission` draft with no section and no `date_submitted`; 5462 is not in the local dump.
- Hiding `IncompleteSubmission` papers from "Search preprints": separate issue, as the ticket says.
- Excluding the row instead of the editor cell in "Pending Issues": would hide papers from issue planning.
- A separate `DirectorListMixin`: one more class for four lists that already share `DirectorPending`.
**Follow-ups:**
- Shared helper for the author predicate (3 copies) and a shared `_make_author` test helper (4 copies).
- `OpenReviewMixin` (`mixins.py`) gives any journal-level director access to a review assignment by ID, including on their own paper; pre-existing, only reachable by guessing an ID.
- `test_audit_pages_share_one_scores_help_modal` fails on `wjs-develop` too (expects `modal-xl`); unrelated.
- Run the issue's functional test steps on a test environment and comment the result on #3013 (not done locally); also open "Pending Issues" with a director-author whose paper is in a special issue.
**Refs:** wjs/specs#3013; commits 57e057e9, a0087267, 81cc9f20, e820ed2c on `feature/issue-3013-director-author-visibility`; spec `artifacts/specs/2026-10-29-director-author-visibility-design.md`, plan `artifacts/plans/2026-10-29-director-author-visibility.md`.
Eval: 75% — artifacts/evaluations/2026-10-30-director-author-visibility.md
