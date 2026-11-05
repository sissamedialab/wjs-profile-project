# Evaluation — publisher-confirmation-gate

- **Date:** 2026-10-15
- **Branch:** feature/2914-publisher-confirmation-gate (working tree vs 9fdbc84d, uncommitted)
- **Task:** wjs/specs#2914
- **Coverage:** full (9 modified files, migration 0021, new test module; spec and plan read)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | R1–R6 pinned by tests (gate `logic__production.py:1873`, JCOM unaffected); end-to-end JCAP publish not exercised (6 pre-existing galley/publication failures on wjs-develop) |
| Testing | 5 | 15 | 39 tests (28 functions, parametrized) in `tests/test_publisher_confirmation.py`, Red→Green per task; stale-revoke and lost-update tests mutation-checked RED against old code |
| Security | 4 | 15 | EO-only `test_func`, POST-only (405 on GET), CSRF form, `confirmed` whitelisted to `1`/`0` (`views__production.py:510`); `objects.get` 500 on bad pk follows sibling views |
| Code quality | 4 | 15 | Mirrors `TogglePublishableFlag` idiom; pre-commit (black/isort/flake8/djLint) clean; 4-state list duplicated between `SetPublisherConfirmedFlag._check_conditions` and state classes |
| Maintainability | 4 | 15 | Single source of truth on model (`models.py:728-737`), journal scoping via setting, one shared action (`states.py:526`), opt-in `disabled_cause` (`states.py:437`) |
| Error handling | 4 | 10 | Specific `ValueError`s with actionable messages ("already recorded/revoked"), atomic + `update_fields` (`logic__production.py:1130`); no success message after set (deferred minor) |
| Documentation | 4 | 10 | Docstrings on all new units, setting commented, spec/plan amended post-review; prose docs pending doc-sync |

## Total
**83%** — Feature complete and well tested; review findings fixed with mutation-verified tests; remaining items are deferred minors.
