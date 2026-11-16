# Evaluation — ac-dismissal-backend

- **Date:** 2026-11-06
- **Branch:** feature/issue-3272-ac-dismissal-backend (working tree, uncommitted)
- **Task:** wjs/specs#3272 (backend part; UI waits for #3367)
- **Coverage:** full — all 14 changed files plus the new migration, panel template and migration-graph test read in full; spec `artifacts/specs/2026-11-04-ac-dismissal-backend-design.md`

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Every backend item in the #3272 technical notes is met and tested; manual steps 6, 7, 9, 11, 12, 13 passed locally; EO read-only fallback in `managers.py::visible_to` deviates from the issue text, pending Iacopo's answer (note 81236) |
| Testing | 4 | 15 | `test_attentionconditions.py` 59 passed (rerun 2026-11-06); full `wjs_review` 1517 passed, 1 env-only failure (DNS to /pubdata); RED→GREEN recorded for the review fixes; `makemigrations --check` not run; e2e skipped by choice |
| Security | 4 | 15 | `ToggleAttentionConditionDismissalView`: POST only, `test_func` checks ownership, AC fetched with `article=workflow.article` (404 on mismatch), logic re-checks ownership under `select_for_update`; workflow lookup not scoped to `request.journal` (ownership still enforced) |
| Code quality | 4 | 15 | Follows the repo patterns (dataclass logic + form + view `form_valid` try/except, queryset helpers, hyphenated URL name); pre-commit passes on all changed files |
| Maintainability | 4 | 15 | `ArticleAttention` + `get_articles_attention` give the frontend one read API; legacy `_prefetched_acs`/`legacy_message` kept alongside it until the frontend switches, which is a temporary dual path |
| Error handling | 3 | 10 | Toggle errors become a non-field error, rendered in the panel; but the error block sits inside `{% if attention.has_any %}`, so it can be invisible, and `upsert_ac`'s two writes are not wrapped in one `transaction.atomic()` |
| Documentation | 4 | 10 | `wjs_review/CLAUDE.md` "Attention conditions (ACs)" section rewritten; docstrings on all new public units; spec and plan in `artifacts/` |

## Recommendations
- Error handling: move the non-field error block outside `has_any` in `attention_panel.html`, and wrap `upsert_ac` in `transaction.atomic()` (both already on the deferred-minors list).

## Total
**78%** — complete and well-tested backend for AC dismissal; minor robustness gaps remain, and the EO fallback is still waiting for Iacopo's confirmation.
