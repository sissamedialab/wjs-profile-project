# Plan: intro text on the Editor's decision page without referee reports

Spec: `artifacts/specs/2026-10-27-editor-decision-page-labels-design.md` (wjs/specs#3277).
Branch: `feature/issue-3277-editor-decision-page-labels` (repo root).

## Steps

1. **Red.** In `wjs/plugins/wjs_review/tests/test_views.py` add three tests that GET `wjs_article_decision` as the
   assigned editor of `assigned_article` (setting `reviewer_report_type` per journal with `override`/`save_setting`
   helpers already used in the suite):
   - no reports, `text` journal: response contains "Please write your report to motivate your decision." and not
     "can be written in LaTeX";
   - no reports, `tex` journal: contains both sentences;
   - with a submitted report (`assigned_article_with_reviewer` flow): contains the old intro and not the new text.
   Run from `janeway/src` and confirm they fail for the right reason.
2. **Green.** In `wjs/plugins/wjs_review/templates/wjs_review/make_decision/decision.html` make `editor_report_info`
   depend on `submitted_reviews` and `tex_review_allowed`; keep the existing string for the has-reports case. Match
   the file's 2-space indentation and djlint rules (`.claude/rules/templates-django.md`).
3. **Translations.** Check whether `wjs/locale/en/LC_MESSAGES/django.po` is edited by hand in this repo's recent commits;
   add the two new msgids there only if so.
4. **Check.** Re-run the three tests plus the existing decision/view tests (`test_views.py -k decision`); run
   `pre-commit run --all-files`.
5. **Wrap-up** (flow steps 9-11): code-eval, doc-sync, activity-log; changes left in the working tree for commit
   review. No Playwright (template text only); no towncrier fragment (no `changes/` directory).
