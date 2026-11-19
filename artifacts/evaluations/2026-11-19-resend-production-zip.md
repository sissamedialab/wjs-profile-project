# Evaluation — resend-production-zip

- **Date:** 2026-11-19
- **Branch:** feature/issue-3450-resend-production-zip (138e7dc7..78de1b03)
- **Task:** wjs/specs#3450
- **Coverage:** full (all touched files read; the suite was run on the touched areas, not the whole repo)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | All spec goals met; 21 tests in `test_resend_production_zip.py` pass; the independent review's critical bug (500 once typesetting started) is fixed and tested; no state guard after `rollback_acceptance` (deferred M2) |
| Testing | 4 | 15 | Every fix seen RED then GREEN; failure paths covered; view tests mock the zip build, bare asserts remain in a few tests (M5, M6) |
| Security | 4 | 15 | POST-only, EO-only (`test_view_refuses_non_eo_and_does_not_send`); role check is global, not journal-scoped, as in the sibling view |
| Code quality | 3 | 15 | Pre-commit clean, conventions followed; settings lookup duplicated in `_check_requirements` (M3), view builds its message with `hasattr(e, "messages")` (`views__production.py`) |
| Maintainability | 4 | 15 | Composition over `SendProductionXMLToPublisher`, single `ac_service` predicate shared by condition and guard; in-memory stage forcing is a contained but real coupling to the export rule |
| Error handling | 4 | 10 | Raise-after-commit keeps the refreshed attention condition (test `test_resend_failure_keeps_ac_with_new_message`); no lock against double POST (M1), unknown pk gives 500 (M4) |
| Documentation | 4 | 10 | Docstrings on new units, plugin `CLAUDE.md` updated; spec and plan not yet updated for the stage forcing and `PublicationInProgress` |

## Recommendations
- Code quality: reuse `sender._get_send_function()` in the guard and replace the `hasattr` message handling with separate `except` clauses.

## Total
**77%** — works and is well tested; remaining gaps are minor hardening items (see the ledger's deferred minors).
