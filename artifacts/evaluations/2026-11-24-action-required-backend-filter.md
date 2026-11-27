# Evaluation — action-required-backend-filter

- **Date:** 2026-11-24
- **Branch:** feature/issue-3482-action-required-backend-filter (working tree, base 74a23512, nothing committed)
- **Task:** wjs/specs#3482
- **Coverage:** full (managers, filters, views hunks, 2 templates and the test module read in full); browser steps T-1 to T-8 not run

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 3 | 20 | All behaviours in the spec pass (57 tests, 231 with related suites); real JCAP run: 85/4/173 papers filtered, defaults right. But list pages run 7 queries vs the spec's anti-goal of 5, and T-1..T-8 were not run in a browser |
| Testing | 4 | 15 | RED→GREEN seen per task and for the two review fixes (`test_sort_and_pagination_links_of_the_eo_default_view_keep_the_filters`); real DB, no mocks. Pagination test does not exercise `Max`/`distinct()` with revisions |
| Security | 4 | 15 | `action_required` validated by a `ChoiceFilter`, hidden input only for "0"/"1" (`filters_visibility.html`), ACs scoped to `request.user`; sec-scan hits are 7 pre-existing `mark_safe` calls, none in the diff |
| Code quality | 4 | 15 | pre-commit (black, isort, flake8, djlint) clean; one shared rule in `managers.py:20-32`. Minor: `field_name="pk"` placeholder on the method filter, fixtures in the test module, hard-coded URLs |
| Maintainability | 4 | 15 | Filter and annotation share `_active_ac_exists`; `filters.py` reuses `has_ac` through an implicit coupling to the annotation name |
| Error handling | 4 | 10 | Anonymous guarded (`views.py:352`), unknown value invalid and hides nothing; an invalid value also drops the other filters (view's existing behaviour) |
| Documentation | 3 | 10 | Docstrings on every new method; `wjs_review/CLAUDE.md` still has stale helper names and no `with_has_ac`/`requiring_attention` in the managers table |

## Recommendations
- **Functionality:** decide the +2 constant queries per list page (memoize the EO lookups per request, or amend the spec) and run T-1..T-8 on a test environment.
- **Documentation:** update the attention section and managers table of `wjs_review/CLAUDE.md` (doc-sync).

## Total
**74%** — behaviour and tests are solid after the review fixes; the extra queries and the unrun browser steps keep it from the top band.
