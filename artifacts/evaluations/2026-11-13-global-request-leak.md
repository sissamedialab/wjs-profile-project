# Evaluation — global-request-leak

- **Date:** 2026-11-13
- **Branch:** bugfix/issue-302-global-request-leak (working tree vs origin/wjs-develop 6b982987)
- **Task:** wjs/wjs-profile-project#302
- **Coverage:** full (permissions.py, tests/conftest.py, tests/global_request.py, 3 new test files)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 5 | 20 | Issue reproduction 3 passed (2 failed before). Full suite with `-n3`: 2049 passed; the 18 failures are environmental (opentelemetry, `cas_url`) |
| Testing | 4 | 15 | RED→GREEN for helper, fixture-active and missing-hijacker tests. The no-warning guard test was added after the fix, and the run without xdist was skipped |
| Security | 4 | 15 | No trust-boundary input. The warning logs only the account pk (`permissions.py:326`) |
| Code quality & best practices | 4 | 15 | pre-commit (black, isort, flake8) passes. The test setup repeats the `process_request` call that `fake_request` already makes (`test_get_hijacker.py:21`) |
| Maintainability & flexibility | 4 | 15 | Reset logic lives in a single `isolated_global_request()` (`tests/global_request.py:10`). The fixture is a two-line wrapper |
| Error handling | 4 | 10 | Specific `Account.DoesNotExist` with a warning, and `AttributeError` handling unchanged (`permissions.py:323-327`). `views.py:893` would log the warning twice |
| Documentation | 4 | 10 | Docstrings on the helper, the fixture (cites #302) and `get_hijacker()`. No prose docs are affected |

## Total
**84%** — Both fixes are correct and minimal, verified by the reproduction and the full suite. The gaps are minor test polish.
