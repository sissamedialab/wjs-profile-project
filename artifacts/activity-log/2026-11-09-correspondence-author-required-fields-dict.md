## 2026-11-09 — Correspondence-author rules moved into a per-journal required-fields dict
**What:** The JCOM/JCAP/default rules are now data (`SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS`, entries `{label, paths, allowed?}`) evaluated by one helper, `missing_required_fields(user, journal)`, in wjs-submission. The `*_correspondence_author_missing_fields` functions and settings are gone; the `*_correspondence_author_validation` functions stay as injection points and now take `(user, journal)`.
**Why:** The validation and missing-fields functions encoded the same rules twice, and the validators were identical in shape. One declarative source also gives the labels shown in step 4. Keeping the validation functions preserves open/closed: a journal with unusual logic injects its own function without refactoring.
**Decisions:** Plain-dict entries with any-of paths and an optional `allowed` set (profession must be a `PROFESSIONS` key, `0` valid, no truthiness test). Validation gets the journal so JCOM and JCOMAL read their own entries. `PROFESSIONAL_LINKS_LABEL` moved to `constants.py` because settings cannot import models. Exact equivalence is proven by tests that carry frozen copies of the old validators (f84bae5c, 23be0c8^) and the old missing-fields functions, swept over every field combination. CHANGELOG.md was not hand-edited (generated at release).
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + writing-plans | ≈60k | ≈45m |
| Implementation | superpowers:executing-plans (inline) | ≈150k | ≈40m |
| Review | superpowers:requesting-code-review (opus subagent) | ≈108k | ≈2m |
| Review | nephila code-eval, doc-sync | ≈15k | ≈5m |

**Considered & dropped:** Per-field check functions (too heavy); plain truthiness for profession (changes behavior); a deprecation period for the old settings (both repos are ours).
**Follow-ups:** Mark the profile form fields required from the dict's paths (needs stable keys); clear error for an override without a `None` entry; test for a missing JCOMProfile; a stale worktree `.claude/worktrees/issue-3271-remove-salutation` still has the one-argument validators and must be rebased before merge.
**Eval:** 78% — artifacts/evaluations/2026-11-09-single-author-validation.md
**Refs:** feature/issue-2728; spec `artifacts/specs/2026-11-09-single-author-validation-design.md`.
