# Evaluation — single-author-validation

- **Date:** 2026-11-09
- **Branch:** feature/issue-2728 in wjs-profile-project and wjs-submission-project (working tree, uncommitted)
- **Task:** — (continuation of issue 2728 work; no tracked item recorded)
- **Coverage:** full (both repos' diffs read; Janeway's `Account` source not read, behavior inferred from passing tests)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Dict + `missing_required_fields` replace both rule sets; sweeps over every field combination match the frozen old validators and old label lists (P `tests/test_account_validation.py`, S `tests/test_account_validation.py:84`) |
| Testing | 4 | 15 | RED seen first (ImportError); mutation checks failed as expected (dropped institution entry, dropped `allowed`); 392 then 111 passed; no test for a missing `jcomprofile` |
| Security | 4 | 15 | No input crosses a boundary: dotted paths come from settings only; no secrets or dynamic code |
| Code quality | 4 | 15 | pre-commit (ruff, black, isort) clean in both repos; `jcom_/jcap_` validators are identical one-liners by design |
| Maintainability | 4 | 15 | Rules are data; injection point kept with `(user, journal)` contract; step 4 still reads the dict only (documented) |
| Error handling | 3 | 10 | Malformed entry or override without a `None` key raises bare `KeyError`/`TypeError`; `RelatedObjectDoesNotExist` propagates as before, not pinned by a test |
| Documentation | 4 | 10 | Docstrings on all touched public units; settings comments updated; `CHANGELOG.md` is release-generated, so not edited |

## Recommendations
- Error handling: fail with a clear message when an override lacks the `None` entry or an entry lacks `label`/`paths`, and pin the missing-JCOMProfile behavior with a test.

## Total
**78%** — behavior proven equivalent and lint-clean; weakest spot is configuration-error handling.
