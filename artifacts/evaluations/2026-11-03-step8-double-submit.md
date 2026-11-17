# Evaluation — step8-double-submit

- **Date:** 2026-11-03
- **Branch:** bugfix/issue-3233-step8-double-submit in wjs-submission-project and wjs-profile-project (working tree)
- **Task:** wjs/specs#3233
- **Coverage:** full — both repos evaluated together (same report in both); Task 6 (version bump) intentionally not done yet

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Lock + redirect in `step8/views.py` `post()`; race tests through the real view pass; read-only report on the 2026-11-03 production dump lists exactly 3836 and 4351 as the analysis found |
| Testing | 4 | 15 | RED→GREEN for every task (race tests fail without the fix: A not blocked, `TransitionNotAllowed`, `Http404`); 5 + 7 + 16 new tests; JS only checked ad hoc in a harness page |
| Security | 4 | 15 | Lock and "already completed" redirect reuse owner/correspondence-author + journal filters (co-author / other journal → 404, tested); `--repair` ids are `type=int`, ORM only |
| Code quality & best practices | 4 | 15 | pre-commit (ruff / black / isort / flake8) passes in both repos; `# noqa: A003` follows the other commands |
| Maintainability & flexibility | 4 | 15 | `Problem`, `candidate_articles()`, `find_problems()` are small, tested units; detection uses model data only, not per-journal message subjects |
| Error handling | 3 | 10 | `--repair` skips with reasons and exits via `CommandError`; but an unexpected exception on one id stops the remaining ids (deferred review minor M2) |
| Documentation | 3 | 10 | Docstrings on `post`, the command module and helpers; no prose doc of the new command yet (doc-sync next) |

## Recommendations
- Error handling: catch unexpected exceptions per id in `_repair` and report them as "failed" (review M2).
- Documentation: describe `report_double_submissions` and its `--repair` workflow where EO/ops look for management commands.

## Total
**76%** — fix and tooling are correct and well tested; minor robustness and docs gaps remain.
