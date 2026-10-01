# Evaluation — ac-accepted-and-appeal-fix

- **Date:** 2026-09-29
- **Branch:** bugfix/issue-3174-ac-accepted-appeal (working tree vs ecbb2b5c)
- **Task:** wjs/specs#3174
- **Coverage:** full (6 modified files, 2 new files: migration 0018, test_attentionconditions_accepted.py)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | R1–R5 met: appeal resolve `logic.py:2339-2343`, AC `ac_service.py:110,758,1123`, creation `logic__production.py:161`, resolve in `ConfirmProductionReadiness` and `rollback_acceptance.py:46`; success branch of `VerifyProductionRequirements` doesn't resolve (unreachable today) |
| Testing | 4 | 15 | 12 new/changed tests, each seen RED→GREEN; mutation of the resolve line → 2 failures; AC modules 33 passed; author-is-EO case not pinned, view test hard-codes the URL |
| Security | 4 | 15 | Permission check kept via `has_transition_perm` (source state + `has_eo_role_by_article`) in `ConfirmProductionReadiness._check_conditions`; non-EO refusal tested; no new inputs |
| Code quality & best practices | 4 | 15 | Follows the `# -- Materialized AC updates --` pattern and the `except ValueError` view convention (`views__production.py:665`, 6 siblings); black/isort/flake8 clean |
| Maintainability & flexibility | 3 | 15 | State-exit resolves stay scattered per exit path: every new way out of Accepted must remember `ACCESS_MODE_TO_CHECK` (the reviewer found one, `rollback_acceptance`) |
| Error handling | 4 | 10 | Transition + AC update in one `transaction.atomic()`; `ValueError` → the same user message as before; fixed the pre-existing `TypeError` in `rollback_acceptance.py:60` |
| Documentation | 4 | 10 | Docstrings on the new constant, evaluator, logic class and migration module; no prose AC catalog in the repo; CHANGELOG is generated at release |

## Recommendations
- Maintainability: open a follow-up to sweep state-scoped event-based ACs on state exit centrally (e.g. extend `_resolve_stale_time_based_acs`), so new exits can't leak.

## Total
**77%** — Correct, well-tested fix following the codebase patterns; the per-exit resolve pattern remains a structural leak risk.
