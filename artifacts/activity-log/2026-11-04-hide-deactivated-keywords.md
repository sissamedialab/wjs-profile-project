## 2026-11-04 — Hide deactivated keywords from keyword lists and selection pages
**What:** The public keywords page, newsletter topics (including default topics for new recipients), profile interests and editor assignment parameters now list or offer only active keywords, via `wjs.jcom_profile.utils.get_active_keywords`. Search and submission are unchanged.
**Why:** specs#3279. Deactivated keywords should stay findable through search only. The issue description missed two of the four views behind `KeywordsHierarchyContextMixin` and the default topics in `AnonymousUserNewsletterRegistration`. A review comment drafted for the issue (not yet posted) covers these gaps.
**Decisions:**
- Newsletter topics and interests that are already selected but deactivated are silently dropped on the next save, which falls out of the filtered querysets.
- Editor `StaffKeyword` rows for deactivated keywords are kept on save, with their director-set weight. Losing a weight would silently change assignment.
- We decided both points ourselves; the confirmation request is in the review comment.
- We use a plain filter helper instead of a custom model queryset, because `Keyword` is a Janeway model.
- `NewsletterTopicForm.clean` now handles `keywords=None`: a stale post with a deactivated keyword used to crash with a 500.
- e2e not needed (backend-only change). Eval: 78% — artifacts/evaluations/2026-11-04-hide-deactivated-keywords.md
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | brainstorming builder-check subagent | ≈104k | ≈2m |
| Review | final whole-branch reviewer (opus) | ≈90k | ≈1m |

**Considered & dropped:** keeping the editor-parameters page out of scope. On hierarchical journals, grouped deactivated keywords are already hidden there since wjs-submission `62a6972`, so the page could not be left untouched cleanly.
**Follow-ups:**
- The interests and editor-parameters templates render no form errors, so a stale post is rejected without any message.
- Tests are thin in three places: reactivation (helper level only), invalid choice (newsletter form only) and hierarchical selection pages.
- Post the review comment on the issue and update its test steps (JCAP plus a hierarchical journal).
- Locally, run tests with `--create-db`: a stale reused DB makes the `brake_on` tests fail.
**Refs:** wjs/specs#3279; artifacts/specs/2026-11-03-hide-deactivated-keywords-design.md; artifacts/plans/2026-11-03-hide-deactivated-keywords.md
