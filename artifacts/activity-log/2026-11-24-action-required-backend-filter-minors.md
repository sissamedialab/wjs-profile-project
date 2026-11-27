## 2026-11-24 — Minor fixes after the review of the "Action Required" filter
**What:** Closed four of the six deferred minors of the whole-branch review of wjs/specs#3482: the filter no longer crashes for an anonymous user, the buttons have translatable labels and `aria-pressed`, the new tests use fixtures from `conftest.py` and `reverse()`, and a test now checks a paper with several revision requests under editor sorting.
**Why:** The user asked to fix the minors except the first (an unknown `action_required` value drops the other filters, the view's existing behaviour) and the last (the placeholder `field_name="pk"` on the method filter).
**Decisions:** The anonymous guard returns an empty list (an anonymous user has no attention conditions) and sits before the `has_ac` reuse branch. The `editor` fixture is renamed `ac_user` when moved, because a global `editor` fixture exists. `aria-pressed` on the two submit buttons was kept as a judgement call: it is the usual segmented-control pattern, a radio group would need JavaScript.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Implementation | inline fixes, TDD (superpowers:test-driven-development) | ≈60k | ≈25m |
| Review | delta reviewer subagent (Opus) | ≈120k | ≈1m |
| Review | code-eval, doc-sync, activity-log (e2e-testing skipped by choice) | ≈10k | ≈5m |

**Considered & dropped:** Refreshing the `.po` files for the three new strings (same as the 3128 work: they fall back to English until the next bulk refresh). A second editor assignment in the pagination test (it would exercise pre-existing behaviour of `_annotate_editor`, not this branch); the test docstring now says what it covers.
**Follow-ups:** Still open from the previous entry: accept or memoize the 2 extra constant queries per list page; run the issue's steps T-1 to T-8 in a browser; correct the issue text about the author and reviewer Pending lists. Refresh the translation catalogue for the new strings.
**Eval:** 76% — artifacts/evaluations/2026-11-24-action-required-backend-filter-2.md
**Refs:** wjs/specs#3482, MR 1662, previous entry `2026-11-24-action-required-backend-filter.md`, previous evaluation `2026-11-24-action-required-backend-filter.md`.
