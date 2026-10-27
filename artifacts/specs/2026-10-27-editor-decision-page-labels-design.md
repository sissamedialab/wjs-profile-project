# Update the intro text on the Editor's decision page when there is no referee report

- **Issue:** wjs/specs#3277 ("Update labels on Editor's decision page")
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-10-27
- **Status:** approved design (chat)

## Problem

When the editor decides on a paper that has no submitted referee report, the decision page
(`make_decision/decision.html`, view `ArticleDecision`) still shows the intro written for the case where
reports exist ("The following form includes any reports that you have just decided to send the authors ..."),
and does not tell the editor that the form accepts LaTeX.

## Requirements

When the paper has no submitted referee report (`submitted_reviews` is empty):

1. The text above the editor-report field is "Please write your report to motivate your decision." (all journals).
2. For journals where LaTeX reports are allowed (`tex_review_allowed`, i.e. the journal's `reviewer_report_type`
   setting is `tex` or `tex+text`; the issue says all journals but JCOM and JCOMAL), the text also says that the
   report can be written in LaTeX: "The report can be written in LaTeX." (my wording, approved in chat).

When there are submitted referee reports the page is unchanged.

## Non-goals

- No change to the old text, the report table, the "Test PDF" button or the form fields.
- No `journal.code` condition in the template (the lint rule drafted in !1589 forbids it): the per-journal
  difference comes from the existing `reviewer_report_type` setting.
- The assumption that JCOM and JCOMAL are configured as `text` while the other journals allow `tex` is a
  production-configuration fact, not enforced by this change.

## Design

Template-only change in `decision.html`: build the intro (`editor_report_info`) conditionally on
`submitted_reviews` and `tex_review_allowed` with `{% translate %}`/`{% blocktranslate %}`-style `as` variables, and keep the
existing text for the has-reports case. New strings are added to the `en` catalogue only if the repo keeps it
manually (other catalogues are left to the usual makemessages workflow).

## Testing

View tests on `wjs_article_decision` with an assigned article and its editor: no reports + text journal shows the
new intro and not the LaTeX sentence; no reports + `reviewer_report_type=tex` shows both; with a submitted report
the old intro is shown and the new text is absent.
