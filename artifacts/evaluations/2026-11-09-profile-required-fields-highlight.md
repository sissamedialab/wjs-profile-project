# Evaluation — profile-required-fields-highlight

- **Date:** 2026-11-09
- **Branch:** feature/issue-2728-submission-required-highlight in wjs-profile-project and wjs-submission-project (working tree, uncommitted)
- **Task:** — (stacked on feature/issue-2728; no tracked item recorded)
- **Coverage:** full (both diffs and new files read; rendered CSS of badge/banner not inspected in a browser)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | All spec goals met: hints in settings, `get_submission_requirements`, Required help text, banner, badges, affiliations note; known limit: session `next` is not tied to a journal (inherited from the back-link mechanism) |
| Testing | 4 | 15 | RED seen for each task; 21 new tests in `test_profile_required_fields.py` + 3 in S; review fix pinned RED→GREEN; "no next" test covers 4 pages, not password/keywords/roles |
| Security | 4 | 15 | Only developer/translation strings reach `required_help_text`; banner labels autoescaped; `next` host check reused |
| Code quality | 3 | 15 | pre-commit clean; requirements computed twice per form page (`views.py` + tag); safe-URL check still duplicated in `ProfileBackToSubmissionView` |
| Maintainability | 4 | 15 | Location hint lives next to the rule in settings; S exposes small public helpers; link `fields` repeat the `paths` without the prefix (drift risk) |
| Error handling | 3 | 10 | Tag reads `context["request"]` (KeyError without it, same as `get_profile_section`); stale `next` shows highlights after an abandoned submission |
| Documentation | 4 | 10 | Docstrings on all new public units, settings comments explain the `profile` key; CHANGELOG is release-generated, not edited |

## Recommendations
- Code quality: memoize `get_submission_requirements` per request and reuse `safe_back_to_submission_url` in the back view.
- Error handling: bind `next` to the journal it was set for (or clear it when leaving the submission flow) and use `context.get("request")` in the tag.

## Total
**75%** — feature complete and tested; weak spots are the per-request recomputation and the unbound session `next`.
