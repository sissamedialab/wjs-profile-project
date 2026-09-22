# Evaluation — author-filter-frozen-authors

- **Date:** 2026-09-17
- **Branch:** `bugfix/issue-3048-filter-author-page-on-frozen-authors` (working tree, no commits yet)
- **Task:** [specs#3048](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3048)
- **Coverage:** full — `wjs/jcom_profile/views.py` (+22/-3), `wjs/jcom_profile/tests/test_views.py` (+114), plus the untracked spec and plan under `docs/superpowers/`

## Scores

| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Root cause fixed at source (`views.py:765` → `frozenauthor__author`); 87 passed / 1 skipped / 2 xfailed / 1 xpassed. Pre-merge data audit not yet run, and the reported symptom partly persists via a separate `wjs-themes` defect (below). |
| Testing | 4 | 15 | TDD throughout; all three new tests verified Red→Green (view change stashed; `distinct()` removed). Gap: the `keywords__in` / `keywords__pk` members of `MULTI_VALUED_FILTERS` are untested. |
| Security | 4 | 15 | `stage=STAGE_PUBLISHED` and `date_published__lte` untouched, so the wider join cannot surface unpublished work; URL kwarg is an `int` converter + `get_object_or_404`. No `sec-scan` run this session. |
| Code quality | 4 | 15 | Reads like surrounding code; `pre-commit run --all-files` clean (17 hooks); comments state *why*, not what. Minor: `MULTI_VALUED_FILTERS` restates key names declared in `_get_filters()`. |
| Maintainability | 3 | 15 | `views.py:732` duplicates the literal filter keys from `views.py:761,765`. Renaming a key there silently disables deduplication — no test or assertion links the two. |
| Error handling | 4 | 10 | Unknown author pk → 404 via `get_object_or_404` (`test_filter_articles_by_author_not_found_error` passes); no new failure paths, no swallowed exceptions. |
| Documentation | 4 | 10 | Spec + plan saved and amended post-review; docstrings on all three new tests explain the Janeway signal mechanics; corrected the stale `_get_filters()` docstring. `CHANGELOG.md` entry still missing (convention needs the MR number). |

## Recommendations

- **Maintainability:** derive `MULTI_VALUED_FILTERS` from the lookups themselves, or add an assertion/test that every member is a key `_get_filters()` can emit — otherwise a future rename disables `.distinct()` with no failing test.

## Out-of-scope defect found

`article_documents.html` in both themes builds author links from the `FrozenAuthor` pk instead of the `Account` pk (`wjs-themes`, `wjs-bootstrap:45` and `JCOM-theme:35`). Separate repo, separate work item.

## Total

**77%** — correct, minimal and well-tested fix at the right layer; held back by a duplicated-constant seam and validation steps that are documented but not yet executed.
