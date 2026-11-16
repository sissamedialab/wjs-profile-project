## 2026-10-23 — EO notification for IoP affiliation change in revision
**What:** On IoP journals (`wjs_submission.settings.IOP_JOURNALS`, default `["JCAP"]`), when "Submit revision" / "Update metadata" changes the paper's affiliation, `PopulateRevisionStep4` sends EO an unread system message (timeline + email) through the new `AffiliationChangedInRevisionNotification`. Subject and body come from two new `wjs_review` settings: `affiliation_changed_in_revision_subject` and `_body`.
**Why:** specs#3185. EO must check the new affiliation against the manuscript and verify access mode/copyright/licence. Until now nothing told them the affiliation had changed.
**Decisions:**
- Unread EO message rather than a status filter on paper lists (user + gamboz): `read_by_eo` already behaves like an attention condition.
- Compare by `ControlledAffiliation` pk; a change of corresponding author also counts (user's call). Accepted cost: a co-author at the same institution triggers an extra message. `None` → value counts. A falsy `affiliation_pk` never fires, because the old affiliation is kept.
- Hooked directly in step 4, before the overwrite, following the step-7 special-request precedent. No new `SubmissionEvent`, which would have needed a change in wjs-submission.
- The email goes out before the revision transaction commits, the same as every other revision notification. No `on_commit` deferral.
- System actor, not the corresponding author: the submitter may be the owner.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | Explore subagent (code recon) | ≈200k | ≈4m |
| Design | builder-check subagent | ≈90k | ≈2m |
| Design | superpowers:brainstorming + writing-plans (inline) | ≈60k | ≈15m |
| Implementation | superpowers:executing-plans (inline, TDD) | ≈50k | ≈15m |
| Review | code-reviewer subagent (opus) | ≈90k | ≈2m |
| Review | code-eval + doc-sync (inline) | ≈15k | ≈5m |

**Considered & dropped:**
- "With affiliation's change" status filter on the paper lists.
- A new attention condition.
- Comparing by organisation, which is more complex and has an unclear benefit.
**Follow-ups:**
- Confirm with EO/PO whether JSTAT/JINST should join `SUBMISSION_IOP_JOURNALS`. This is the setting's first real consumer.
- Should a notification-rendering failure abort the author's revision? The step-7 precedent has the same issue.
- Deferred review minors:
  - the body shows the pre-revision title if the same revision also changes it;
  - a null old corresponding author renders "()";
  - the step-4 test does not assert "Old Dept".
- Local test gotchas:
  - pytest needs `-p no:freezegun` and a scratch settings module that adds a no-op `SkipMigrations.setdefault` (django-debug-toolbar 7).
  - Full suite: 2 env-only failures, `test_imu_step1_accessible_when_logged_in` (missing foundationform template) and `test_publication_multiple_articles` (local service DNS).
**Refs:** wjs/specs#3185 (related #3186, #3083); spec `artifacts/specs/2026-10-23-iop-affiliation-change-notification-design.md`; plan `artifacts/plans/2026-10-23-iop-affiliation-change-notification.md`.
Eval: 78% — artifacts/evaluations/2026-10-23-iop-affiliation-change-notification.md
