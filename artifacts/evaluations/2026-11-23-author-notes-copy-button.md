# Evaluation — author-notes-copy-button

- **Date:** 2026-11-23
- **Branch:** feature/issue-3128-author-notes-copy-button (working tree, uncommitted)
- **Task:** wjs/specs#3128
- **Coverage:** full (5 files; JS read in full but never executed in a browser; supervisor role path not exercised)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 3 | 20 | All spec points implemented (button, html+plain, feedback, error message, per-version ids); clipboard behaviour unverified in a browser — Playwright skipped, T-1..T-5 pending |
| Testing | 3 | 15 | 7 tests Red→Green (`test_author_notes_copy_button.py`), 89 passed with `test_production.py`; no JS test, supervisor untested, `_modal_body` regex fragile |
| Security | 4 | 15 | Notes rendered `\|safe` at the same bleach-cleaned trust level as the existing modal; no new input path (`production_galleyproofing.html:35`) |
| Code quality | 4 | 15 | pre-commit (black, flake8, djlint, prettier) clean; follows `report_editor.js` pattern; duplicate `author_notes_source-N` id on the typesetter pending-proofs view |
| Maintainability | 4 | 15 | Generic `data-copy-source` contract in `clipboard_copy.js`, no `wjs-themes` change, reusable for other modals |
| Error handling | 3 | 10 | Failures caught and shown inline (`clipboard_copy.js` catch), but one blanket catch gives a misleading message for empty-text notes and the message is never cleared |
| Documentation | 4 | 10 | File header documents the markup contract; template comment; no prose docs affected (doc-sync pending) |

## Recommendations
- Functionality: run the issue's T-1..T-5 on a test environment and record the result before hand-over.
- Testing: add a supervisor-role status-page test and replace the `_modal_body` regex with an HTML parser.
- Error handling: distinguish "nothing to copy" from a clipboard failure and hide the error when the modal closes or a copy succeeds.

## Total
**71%** — solid, convention-following change; confidence limited by unexecuted browser behaviour and a few deferred minors.
