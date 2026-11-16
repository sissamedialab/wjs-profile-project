# Evaluation — director-signature-label

- **Date:** 2026-11-11
- **Branch:** bugfix/issue-3187-director-signature-label (working tree on 306a7e91)
- **Task:** wjs/specs#3187 (QA follow-up)
- **Coverage:** full (communication_utils, views, forms, plugin_settings, migration 0024, both test files; spec and plan read)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | `communication_utils.py:357` follows the spec table. Local `migrate wjs_review` gives JCOM/JCOMAL False and the other 5 journals True; reverse leaves only the default. Translated columns are all written, which only matters with a non-English UI. |
| Testing | 4 | 15 | Red→Green observed for each task, plus a mutation check for director-as-editor. Full `wjs_review` suite: 1528 passed. The migration tests use the live (modeltranslation) registry, not historical apps. |
| Security | 5 | 15 | No new input or trust boundary. The role words are fixed literals, and the migration only touches one named setting. |
| Code quality | 4 | 15 | Pre-commit (isort/black/flake8/pyupgrade/django-upgrade) passes. `views.py` compares `role == "Editorial Office"` to add the name. The migration duplicates the setting's pretty name and description. |
| Maintainability | 4 | 15 | One helper drives both signatures (`forms.py:1519`, `views.py:2622`). Directors' wording is a per-journal setting, not hardcoded codes. |
| Error handling | 4 | 10 | 0024 also creates the default `"on"`, so `get_setting` can't raise `DoesNotExist` before `install()`. Overrides are set with `update_or_create`, so re-running is safe. |
| Documentation | 4 | 10 | Docstrings on the helper, the migration and the form context. No prose docs mention these signatures. |

## Total
**83%**: correct and well tested, and the shared helper removes the drift between the two signatures. The MR must flag migration 0024 and the "Director" default for new journals.
