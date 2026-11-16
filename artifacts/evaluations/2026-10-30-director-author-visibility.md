# Evaluation — director-author-visibility

- **Date:** 2026-10-30
- **Branch:** feature/issue-3013-director-author-visibility (e9aaafaa..e820ed2c, 4 commits)
- **Task:** wjs/specs#3013
- **Coverage:** full (all touched files read; the issue's functional test steps were not run on a test environment)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Spec goals met for 4 lists, reminders/toggle/audit pages, audit list, Activity, plus Pending Issues found by review (`views.py:719`, `table_article.html`); full wjs_review suite 1404 passed |
| Testing | 4 | 15 | RED→GREEN per task; frozen/correspondence-only × every list/page, non-author and EO controls (`test_director_author_visibility.py`); one test is parametrized state-agnostic |
| Security | 4 | 15 | Tightens authorization, no new inputs or secrets; `OpenReviewMixin` director-by-ID path left (pre-existing, noted) |
| Code quality | 3 | 15 | Template method removes the override trap; author predicate written 3 times (`managers.py:28`, `communication_utils.py:151`, `views.py:2296`), `_make_author` copied in 4 test files |
| Maintainability | 4 | 15 | One exclusion point in `DirectorPending._apply_base_filters`; `_apply_state_filters` hook; `is_director_and_author` reused by template tag |
| Error handling | 4 | 10 | Toggle falls back to journal-level check when no article/workflow (tested); audit page 403 before 404 |
| Documentation | 3 | 10 | Docstrings on all new/changed units; no prose docs, changelog fragment or issue DoD comment yet (pending doc-sync and towncrier) |

## Recommendations
- Code quality: extract one author-predicate helper (`Q` builder with a prefix) and a shared `_make_author` test helper.
- Documentation: add the towncrier fragment and post the functional-test result on #3013.

## Total
**75%** — correct and well tested, with duplication and the remaining documentation work keeping it from the top band.
