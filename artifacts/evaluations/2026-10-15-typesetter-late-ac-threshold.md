# Evaluation — typesetter-late-ac-threshold

- **Date:** 2026-10-15
- **Branch:** bugfix/issue-3264-typesetter-late-ac-threshold (working tree vs 2abd851e, incl. untracked test modules)
- **Task:** wjs/specs#3264 (stakeholder spec: note 78773)
- **Coverage:** full — `conditions.py`, `logic__production.py`, `test_attentionconditions.py`, the two new test modules, spec and plan

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | `conditions.py:574-618` `today > due` on local dates for typesetter and proofing, `logic__production.py:560-564` local due; RED showed "0 days late" / "Was expected by 2026-10-12" / due 20.10 vs 21.10, all GREEN; evaluators still visit completed old-round assignments (pre-existing, out of scope) |
| Testing | 4 | 15 | 14 new tests (unit + evaluator, midnight/UTC, no-due guard, stale-row resolve), RED→GREEN for each task plus a mutation check on old `conditions.py`; 112 passed in related modules; no test for typesetter-also-EO / multiple rounds |
| Security | 5 | 15 | Pure date arithmetic on DB values; no new inputs, queries, string-built SQL/shell, or secrets in the diff |
| Code quality | 4 | 15 | Matches surrounding plain f-string messages, shared `_days` helper (`conditions.py:569`), pre-commit clean; `_local` test helper duplicated in both new test modules; one test docstring overstates its scenario |
| Maintainability | 4 | 15 | Change confined to two condition functions and one due computation; evaluators untouched; thresholds still driven by the existing settings, no new magic values |
| Error handling | 4 | 10 | Guard clauses return `""` before any timezone call (tested); `is_author_proofing_late` assumes an aware DB datetime — an in-memory `date` would raise (ruled YAGNI, only caller re-reads from DB) |
| Documentation | 4 | 10 | Docstrings on both conditions state the rule and cite specs#3264; spec/plan updated with both amendments; no prose doc references these messages, CHANGELOG is generated at release |

## Total
**83%** — Correct, well-tested fix matching the stakeholder rule; remaining items are minor test polish and pre-existing evaluator behaviour.
