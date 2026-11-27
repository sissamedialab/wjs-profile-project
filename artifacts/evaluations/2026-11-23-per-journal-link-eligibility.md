# Evaluation — per-journal-link-eligibility

- **Date:** 2026-11-23
- **Branch:** bugfix/issue-3392-per-journal-link-eligibility (working tree vs 5886360a, nothing committed)
- **Task:** wjs/specs#3392
- **Coverage:** full (all touched files read; the functional UI steps T-A to T-D of the issue were not run on a test environment)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Spec items A, C, D met (`settings.py:812-849`, `checks.py`); `manage.py check` clean, and E001 shown for a temporary JCAP+facebook rule; UI steps not run |
| Testing | 4 | 15 | RED seen (15 failed; ImportError for `checks`), then 83 passed; some new tests also pass without the change; no HTTP-level step 4 test |
| Security | 4 | 15 | No trust boundary, secrets or dangerous patterns in the diff; `sec-scan` not run |
| Code quality & best practices | 4 | 15 | pre-commit all hooks pass (isort reformatted `apps.py`, re-run clean); follows module style; `profile_fields[None]` read eagerly in `checks.py:36` |
| Maintainability & flexibility | 4 | 15 | Pure function with injectable dicts, tests feed bad data; two explicit entries stay duplicated with `PROFILE_FIELDS`, guarded by the check |
| Error handling | 3 | 10 | A deployment override without a `None` key gives a KeyError traceback instead of E001 (`checks.py:36-37`, deferred minor) |
| Documentation | 3 | 10 | Docstrings on both new public functions; test header "frozen references" and `_old_jcom_validation` docstring stale; spec says `get_required_field_entries` but code reads the settings constant; wjs-submission docstring still lists the known limitation (other repo) |

## Recommendations
- Error handling: resolve the fallback with `.get(code, d.get(None, ...))` or report a missing `None` key as E001 instead of raising.
- Documentation: update the stale test docstrings, correct the spec's Constraints line, and update the wjs-submission "Known limitation" paragraph in its own MR.

## Total
**76%** — requirements met and verified by tests and the live check; points lost on a rough edge in the check's error path, stale docs, and no run of the UI test steps.
