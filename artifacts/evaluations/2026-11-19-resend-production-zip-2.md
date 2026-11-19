# Evaluation — resend-production-zip (review fix)

- **Date:** 2026-11-19
- **Branch:** feature/issue-3450-resend-production-zip (`b0480a85`, review fix for MR !1643)
- **Task:** specs#3450
- **Coverage:** full (`conditions.py`, `logic__production.py`, `states.py`, `CLAUDE.md`, `test_resend_production_zip.py`)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | `conditions.production_zip_resend_blocker` drives both `can_resend_production_zip` and `_check_requirements`; state guard added; 28/28 pass (`--create-db`) |
| Testing | 3 | 15 | `_assert_button_matches_logic` over met / resolved / no send function / 3 states, plus states-sync test; the workflow row lock (`run()`, `select_for_update`) has no test: removing it keeps the suite green |
| Security | 4 | 15 | No new input; permission unchanged (`has_eo_role_by_article`); refusal reasons are fixed translated strings |
| Code quality | 4 | 15 | pre-commit (black/isort/flake8/pyupgrade) passes on touched files; locked instance discarded then `refresh_from_db(fields=["state"])` instead of reused |
| Maintainability | 4 | 15 | Single source of truth for button and logic; `RESEND_PRODUCTION_ZIP_STATES` duplicated with the state declarations but guarded by `test_action_declared_exactly_in_resend_states` |
| Error handling | 4 | 10 | Each blocker returns a specific, actionable message raised as `ValidationError`; a concurrent second resend is refused, not double-sent |
| Documentation | 4 | 10 | Docstrings on every touched unit; `wjs_review/CLAUDE.md` updated (one 172-char line at :128); plan still names the removed `conditions.production_export_send_failed` |

## Recommendations
- Testing: add a concurrency test for the lock (thread-based, `django_db(transaction=True)`, as in `test_step8_double_submit.py`): two resends, send mock called once, second gets the "no failed delivery" error.

## Total
**77%** — The review point is fully addressed and well tested; only the new row lock is unproven by tests.
