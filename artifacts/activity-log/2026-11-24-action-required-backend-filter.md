## 2026-11-24 — "View only with Action Required" as a server-side filter
**What:** The paper-list button that hid rows in the browser is now a django-filter backend filter over the whole list (`action_required=1|0`), every list row is annotated with `has_ac`, and the EO "Pending" list defaults to it for an EO with a workload.
**Why:** ACs are materialized in the database, so the current-page-only button was a bug; EOs clicked it page after page and appeals on late archived pages could be missed (#3457, #3035 merged into #3482).
**Decisions:** Annotate every row (option B) instead of filtering only: measured on `janeway_jcap`, the cost is small and the annotation serves later controls (ordering). The visibility rule lives once, in `managers._active_ac_exists`, so the filter and the row highlight cannot disagree. Buttons stay a two-button group sending `action_required=1|0` as GET submit buttons; an explicit `action_required` (or a `search`) cancels both EO defaults. After the whole-branch review: `eo_in_charge` is set as a pk (sort/pagination links dropped every filter when it held the `Account`), a hidden input keeps the choice through Search/Enter, the filter reuses the annotation instead of rebuilding the rule. Author and reviewer Pending lists have `show_filters = False`, so they never had the buttons.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming (spec, JCAP A/B measurement scripts) | ≈70k | ≈45m |
| Design | superpowers:writing-plans | ≈25k | ≈10m |
| Implementation | superpowers:executing-plans (inline, 4 tasks, TDD) | ≈250k | ≈90m |
| Review | Whole-branch reviewer subagent (Fable) | ≈190k | ≈2.5m |
| Review | e2e-testing (skipped), code-eval, doc-sync, activity-log | ≈25k | ≈10m |

**Considered & dropped:** Option A (filter only, no per-row annotation): slightly cheaper, but gives other controls nothing to build on. A checkbox in the filter form instead of the button group (user kept the buttons). Moving the row highlight to `has_ac` (touches six row templates, not needed). Playwright (skipped by choice: the behaviour is GET parameters covered by Django client tests).
**Follow-ups:** Decide the 2 extra constant queries per list page (7 vs the spec's 5: `has_eo_role` and `get_eo_user` inside `visible_to`; the first A/B measurement missed them because the rule was built outside the timed loop): accept, or memoize the EO lookups per request. Correct the issue wording on author/reviewer Pending lists (GitLab write, not done). Run the issue's functional steps T-1 to T-8 in a browser, including sort/paginate from the EO default view and Search/Enter with "View only" active. Deferred minors: unknown `action_required` value drops the other filters too; anonymous fallback of `filter_action_required` untested; pagination test lacks `Max`/`distinct()` revisions; fixtures in the test module and hard-coded URLs; untranslated button labels and no `aria-pressed`.
**Eval:** 74% — artifacts/evaluations/2026-11-24-action-required-backend-filter.md
**Refs:** wjs/specs#3482 (main), #3035 (merged and closed), #3457, #1606; wiki `Review/AttentionConditions` (LST-10 to LST-12); spec `artifacts/specs/2026-11-24-action-required-backend-filter-design.md`, plan `artifacts/plans/2026-11-24-action-required-backend-filter.md`.
