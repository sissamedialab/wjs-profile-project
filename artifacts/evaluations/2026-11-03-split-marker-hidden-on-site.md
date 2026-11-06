# Evaluation — split-marker-hidden-on-site

- **Date:** 2026-11-03
- **Branch:** bugfix/issue-3235-hide-split-marker (working tree vs 9ed0acd)
- **Task:** wjs/specs#3235
- **Coverage:** full (models.py, 5 templates, 3 test files)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | All 5 spec requirements met; local message 3619587 `display_body` has no marker while `body` keeps it (Django shell check); browser check by the developer still pending |
| Testing | 4 | 15 | RED→GREEN watched for each task; edge cases (several markers, markup-adjacent, empty, all 4 message types, view with SYSTEM + USER side by side); 279 passed / 1 skipped / 1 xfailed on the 3 touched test files |
| Security | 4 | 15 | No new input path; templates keep the existing `|safe` on the bleached stored body; plain string replace, no new markup |
| Code quality | 4 | 15 | Reads like surrounding code; ruff: no new hits in models.py, new hits in tests are the file's existing style (parametrize tuples, unused fixture, magic 200) |
| Maintainability | 5 | 15 | Single `Message.strip_split_marker` knows the marker; `display_body` on `Message` / `Reminder` reused by templates; no data touched |
| Error handling | 4 | 10 | `partition` removes the `ValueError` on bodies with several markers (`models.py` `render_message`), covered by `test_emit_message_email_with_several_markers` |
| Documentation | 4 | 10 | Docstrings on the 3 new units explain the teaser/site split; spec and plan in `artifacts/` |

## Total
**83%** — small, well-contained fix meeting the spec; minors (subject fallback, forward prefill) deferred.
