# Evaluation — staff-workload-parameters-on-role

- **Date:** 2026-10-21
- **Branch:** feature/issue-3093-staff-params-on-role (working tree, uncommitted)
- **Task:** wjs/specs#3093
- **Coverage:** full (`signals.py`, new test module, 4 test files with fixture changes; spec + plan read)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | R1–R3 met (`signals.py:48-84`, 15 new tests pass); EO m2m receiver lacks a `loaddata` guard (reviewer Minor 1) |
| Testing | 4 | 15 | RED seen (4+4 failures) → GREEN; 1550 passed in jcom_profile+wjs_review; no reverse-side non-EO test |
| Security | 5 | 15 | ORM-only `get_or_create`, no input crossing a trust boundary, no secrets |
| Code quality & best practices | 4 | 15 | Matches existing receiver style; pre-commit clean after isort/black autofix |
| Maintainability & flexibility | 4 | 15 | Named `STAFF_PARAMETERS_ROLES`; two single-purpose receivers; tests use `update_or_create` instead of signal switches |
| Error handling | 3 | 10 | No error paths designed; concurrent create may surface `ValidationError` from model `full_clean` (pre-existing, `models.py:228`) |
| Documentation | 4 | 10 | Module + receiver docstrings incl. new-journal caveat; spec/plan saved; changelog fragment pending |

## Recommendations
- Error handling: if concurrent role grants become realistic, catch `ValidationError`/`IntegrityError` around `get_or_create` in the receivers (or drop `full_clean` uniqueness from `StaffWorkloadParameters.save()`).

## Total
**81%** — Solid, minimal fix matching the approved spec; only minor robustness gaps remain.
