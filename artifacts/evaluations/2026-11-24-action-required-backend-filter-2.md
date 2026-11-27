# Evaluation — action-required-backend-filter (after the minor-fix pass)

- **Date:** 2026-11-24
- **Branch:** feature/issue-3482-action-required-backend-filter (commits f970fa61, 4de0758a + uncommitted minor fixes)
- **Task:** wjs/specs#3482
- **Coverage:** full (delta of the pass read in full, previous scope re-checked); browser steps T-1 to T-8 not run
- **Previous:** artifacts/evaluations/2026-11-24-action-required-backend-filter.md (74%)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 3 | 20 | Spec behaviour passes (59 new tests, 233 with related suites); still 7 queries per list page vs the spec's 5 and T-1..T-8 not run in a browser |
| Testing | 4 | 15 | New tests RED→GREEN (anonymous `TypeError` reproduced, `aria-pressed`); fixtures now in `tests/conftest.py`, `reverse()` URLs; editor-sorting test exercises one assignment only |
| Security | 4 | 15 | Anonymous user now gets an empty list instead of a crash (`filters.py:319-325`); choice validated by `ChoiceFilter`; no new `mark_safe` |
| Code quality | 4 | 15 | pre-commit clean; no fixture collisions (`ac_user` rename); `field_name="pk"` placeholder left on purpose |
| Maintainability | 4 | 15 | One shared rule; fixtures reusable from conftest; `filters.py` still couples to the annotation name `has_ac` |
| Error handling | 4 | 10 | Anonymous and unknown values handled; an invalid value still drops the other filters (view's existing behaviour, accepted) |
| Documentation | 4 | 10 | Docstrings on all touched units; `wjs_review/CLAUDE.md` aligned; new msgids not in the `.po` files yet (fall back to English) |

## Recommendations
- **Functionality:** decide the +2 constant queries per list page (memoize the EO lookups per request, or amend the spec) and run T-1..T-8 on a test environment.

## Total
**76%** — the minors are closed and documented; the open query-count decision and the unrun browser steps keep functionality at 3.
