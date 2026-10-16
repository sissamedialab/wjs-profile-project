# Evaluation — jcap-imported-papers-affiliation

- **Date:** 2026-10-07
- **Branch:** feature/issue-3131-imported-papers-affiliation (working tree, base wjs-develop, in wjs-profile-project and wjs-submission-project)
- **Task:** wjs/specs#3131
- **Coverage:** full (all 6 changed and 3 new files in both repos read in full; the two importer call sites verified by reading, no test covers them)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Every spec requirement has a passing test (check, release, finalize, admin); importer call sites `import_logic.py` TYP_TAKES_CHARGE and `import_articles_from_wjapp.py:716` only verified by reading |
| Testing | 4 | 15 | 21 new tests, each seen RED then GREEN; neighbouring files green (33/1 skipped, 22); admin save test has no access mode, so its licence/copyright assertions are weak |
| Security | 4 | 15 | Admin affiliation choices restricted server-side to the correspondence author's + stored one (`forms.py` `_get_affiliation_queryset`), other accounts' affiliations rejected (test); no new input boundary otherwise |
| Code quality | 4 | 15 | pre-commit clean in both repos; helpers reuse existing logic (`VerifyProductionRequirements.log_ready_for_typesetter`, `resolve_all_for_article`); two consecutive `exists()` blocks in the command could merge |
| Maintainability | 4 | 15 | Single-purpose helpers with docstrings; JCAP message found by `render_production_readiness_subject`, shared by sender and importer so they cannot drift |
| Error handling | 3 | 10 | Release runs inside the importer's try/except, SFTP failures flagged as AC by existing code; `finalize_production_readiness_hold` is called outside that try/except (crash instead of "must be imported again") |
| Documentation | 4 | 10 | Docstrings on all new/renamed public functions and on the form; CHANGELOG is compiled at release from MRs, so no entry is due here; spec and plan in `artifacts/` |

## Recommendations
- Error handling: move the finalize call into the importer's error handling (or merge it into the following `exists()` block under the same logging).

## Total
**78%** — Complete and well tested against the spec; the remaining gaps are the untested importer wiring and the finalize call outside the error handling.
