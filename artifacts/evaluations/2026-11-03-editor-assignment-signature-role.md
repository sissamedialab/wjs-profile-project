# Evaluation — editor-assignment-signature-role

- **Date:** 2026-11-03
- **Branch:** bugfix/issue-3187-editor-assignment-signature-role (working tree on 9ed0acd)
- **Task:** wjs/specs#3187
- **Coverage:** full (forms.py, plugin_settings.py, migration 0022, new test file; spec and plan read)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | `forms.py:1507-1519` adds `sender_role_label`; local `migrate wjs_review` rewrote default/JCOM/JCOMAL/JQuant; special-issue editors and journal admins still get an empty role (outside the spec's EO/Director scope) |
| Testing | 4 | 15 | 8 tests in `test_editor_assignment_signature.py` (Red→Green observed); wjs_review suite 1409 passed; migration tests run on the live, not historical, app registry |
| Security | 5 | 15 | No new trust boundary: the label comes from the fixed `LABELS` map/role slug; the migration only applies a fixed regex to stored text |
| Code quality | 4 | 15 | pre-commit (isort/black/flake8/pyupgrade/django-upgrade) passes; the strict ruff profile flags test annotations/docstrings (ANN001/D103/ARG001 fixtures), as in the existing test files |
| Maintainability | 4 | 15 | Reuses `role_for_article` + `role_label`; the migration has module-level, testable `replace_placeholder`/`forwards`/`backwards` and named constants |
| Error handling | 4 | 10 | `replace_placeholder` passes None/empty through; saves only the changed columns (`update_fields`); `role_for_article` never raises (worst case `""`) |
| Documentation | 4 | 10 | Docstrings on the migration module/functions and on the updated context method (wrong `unassign_editor` reference fixed); no prose docs reference this setting |

## Total
**83%** — Small, well-tested fix that follows the spec; the data migration needs reviewer confirmation in the MR.
