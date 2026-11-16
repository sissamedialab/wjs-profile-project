# Evaluation — required-fields review follow-ups (MR 1587 / 178)

- **Date:** 2026-11-11
- **Branch:** feature/issue-2728 (wjs-profile-project fb0b54e8..618e33e6, wjs-submission-project bfc7855..9d60db3)
- **Task:** wjs/specs#2728 (review comments on wjs-profile-project!1587)
- **Coverage:** full (5 commits in P, 1 in S; no e2e, template copy changes only)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Typed entries, generic links help text and rename all demonstrated by tests; hidden link fields still satisfy eligibility (deliberate, documented, open product question) |
| Testing | 4 | 15 | New tests written red first (11 failed before `group_help`); key-pin tests in both repos; page test slices HTML by `wjs-profile-form__title`/`<form` (fragile) |
| Security | 4 | 15 | No new input handling; `forms.py` still `mark_safe`s help text, now also the static `group_help` (translated literal, no user data) |
| Code quality | 4 | 15 | Rename restricted to feature files, step 1 `submission_requirements` untouched; pre-commit clean; `LINKS_LABEL` literal duplicated in tests |
| Maintainability | 4 | 15 | One typed format in S extended in P (`ProfileRequiredFieldEntry`), single source for label/help; `group_help` is the only new knob |
| Error handling | 3 | 10 | TypedDict not enforced at runtime, unknown keys caught only by tests; no new failure paths |
| Documentation | 4 | 10 | Attribute docstrings on every key, spec Amendment 2, both MR descriptions updated; old names remain in historical plan/evaluation artifacts (intended) |

## Recommendations
- Error handling: entries are checked only by tests; a startup system check was considered and deliberately skipped.

## Total
**78%** — solid, behaviour-preserving follow-ups; remaining gap is runtime validation of entries.
