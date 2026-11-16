# Evaluation — hide-deactivated-keywords

- **Date:** 2026-11-04
- **Branch:** bugfix/issue-3279-hide-deactivated-keywords (working tree on `922c5ac`)
- **Task:** wjs/specs#3279
- **Coverage:** full (7 modified modules + new test file read in full)

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | All spec goals wired through `get_active_keywords` (views.py:909, profile/views.py:163, the 3 form querysets, newsletter/views.py:55,62,105); hierarchical selection pages covered only indirectly via the shared mixin |
| Testing | 4 | 15 | 11 tests in test_deactivated_keywords.py, each observed RED→GREEN; weight-preservation test; gaps: reactivation tested only on the helper, invalid choice tested only on the newsletter form |
| Security | 4 | 15 | No new input paths; deactivated pks rejected by `ModelMultipleChoiceField` querysets (test_newsletter_form_rejects_deactivated_keyword) |
| Code quality | 4 | 15 | One helper, no duplication; pre-commit clean; plain filter instead of a model queryset, a justified deviation because `Keyword` is a Janeway model (spec) |
| Maintainability | 4 | 15 | Single source of truth for "active" (utils.py:347); call sites keep their own ordering/exclusions |
| Error handling | 3 | 10 | Fixed the 500 in `NewsletterTopicForm.clean` (newsletter/forms.py:76); interests/editor templates still render no form errors, so a stale post with a deactivated keyword is rejected silently (pre-existing) |
| Documentation | 4 | 10 | Helper docstring and inline comments (forms.py:236, newsletter/forms.py:76); spec/plan in artifacts; issue description update pending via the review comment |

## Recommendations
- Error handling: render `{% bootstrap_form_errors form %}` in personal_interests_edit.html and update_editor_parameters.html (follow-up issue).

## Total
**78%** — Solid and minimal; spec met with real tests. Main gap is pre-existing silent form rejection.
