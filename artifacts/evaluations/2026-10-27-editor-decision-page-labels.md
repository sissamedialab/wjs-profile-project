# Evaluation — editor-decision-page-labels

- **Date:** 2026-10-27
- **Branch:** feature/issue-3277-editor-decision-page-labels (working tree)
- **Task:** wjs/specs#3277
- **Coverage:** full (1 template, 1 test file; 8 template lines, 63 test lines)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | `decision.html` shows the new intro (plus LaTeX sentence when `tex_review_allowed`) only without submitted reports; old text kept otherwise. Which journals allow LaTeX depends on production `reviewer_report_type` values, not verified here |
| Testing | 4 | 15 | Red→Green seen: 3 no-reports cases failed before the change; `test_views.py` 70 passed after; with-reports guard test; technical-revision decision page not covered |
| Security | 4 | 15 | Static translated strings rendered by the template engine (autoescaped); no input or setting-write change |
| Code quality | 4 | 15 | Reads like the file's existing `translate ... as` pattern; flake8/djLint/black pass; two near-duplicate sentences instead of concatenating translations (deliberate, for i18n word order) |
| Maintainability | 4 | 15 | Per-journal difference comes from the existing `reviewer_report_type` setting, no `journal.code` branch; LaTeX sentence is duplicated across two msgids |
| Error handling | N/A | 10 | No failure path touched |
| Documentation | 4 | 10 | Spec and plan under `artifacts/`; no prose docs mention this text; `en` catalogue left to makemessages like recent features |

## Recommendations
- Functionality: confirm in production that JCOM and JCOMAL have `reviewer_report_type=text` and the other journals `tex`/`tex+text`.

## Total
**80%** — small, tested template change; the per-journal mapping is a configuration assumption to confirm.
