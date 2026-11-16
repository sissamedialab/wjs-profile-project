# Evaluation — iop-affiliation-change-notification

- **Date:** 2026-10-23
- **Branch:** feature/issue-3185-iop-affiliation-change-notification (working tree, base wjs-develop)
- **Task:** wjs/specs#3185
- **Coverage:** full (`wjs_review/logic.py`, `plugin_settings.py`, `tests/test_logic.py`; spec + plan)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | All spec triggers met (`PopulateRevisionStep4._affiliation_changed`, 6-case test green); pre-revision title shown if title also changes (cosmetic) |
| Testing | 4 | 15 | 8 new cases, RED→GREEN observed (ImportError, then 3 expected failures → pass); `-k "revision or affiliation"` 142 passed; full suite 1541 passed, 2 env-only failures |
| Security | 4 | 15 | No new user input; templates come from admin-editable settings, same pattern as `AccessModeSpecialRequestNotification` |
| Code quality | 4 | 15 | Mirrors step-7 precedent and dataclass+`run()` convention; pre-commit clean; `affiliation_pk` read twice (minor) |
| Maintainability | 4 | 15 | Single-purpose notification class; IoP list reused from `wjs_submission.settings.IOP_JOURNALS`; copy editable per journal |
| Error handling | 3 | 10 | A missing/broken setting template would raise inside `AuthorHandleRevision.run()` and abort the revision submission; not a deliberate choice, inherited from precedent |
| Documentation | 4 | 10 | Docstrings on new class/helper, inline comment on pre-revision capture; spec + plan in `artifacts/` |

## Recommendations
- Error handling: consider whether a notification-rendering failure should abort the author's revision; if not, catch and log it (would also apply to the step-7 precedent).

## Total
**78%** — Spec-complete, well-tested addition following existing patterns; only the failure-propagation choice is unexamined.
