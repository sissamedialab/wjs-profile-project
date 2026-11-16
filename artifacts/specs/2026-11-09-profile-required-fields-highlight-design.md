# Profile: highlight the fields required for submission

Date: 2026-11-09. Language: English. Repos: `wjs-profile-project` (P, main), `wjs-submission-project` (S, small API
addition). Branch: `feature/issue-2728` (same as part 1, which introduced the per-journal required-fields dict;
see `2026-11-09-single-author-validation-design.md`).

## Intent

An author blocked at submission step 4 because their profile is incomplete is sent to the profile pages
("Back to submission" link). Today they must guess which fields and tabs matter. Use the required-fields dict
(the single source of truth for validation and step 4) to show them, on the profile pages themselves, what the
journal requires and what is still missing. Other users of the profile (editors, reviewers, EO, authors not
coming from submission) must see nothing new: those fields are not relevant to them.

## Goals

1. **Trigger.** Everything below appears only when the profile view was reached from the submission, i.e. the
   existing `BaseProfileEditView.back_to_submission_url` is set (session `next`), the user is authenticated and a
   journal is in context. Otherwise no change at all.
2. **Journal rules.** Use `CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` of the current journal (`None` fallback), as
   validation does. A user in the journal's whitelist (eligible regardless of the fields) sees nothing, like in
   step 4.
3. **Location hint in the dict.** P's `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` entries get an optional
   key `"profile": {"section": <profile tab>, "fields": [<form field names>]}`; `section` is the url name of the
   tab (`core_edit_profile`, `core_edit_profile_affiliations`, `core_edit_profile_email`,
   `core_edit_profile_additional`). S ignores the key (S's evaluator reads only `label`, `paths`, `allowed`).
   Entries without the key (e.g. "Active account") are never shown on the profile pages; they still block
   validation and still appear in step 4.
4. **Required help text (always on, filled or not).** Form fields named in a hint of the journal's rules get
   `required_help_text(<existing help text>)` from S (`plugins.wjs_submission.helpers.forms`), i.e. **Required**
   followed by the field's existing help text, the same pattern as the submission steps. For an entry with more than
   one field ("at least one of": name, professional links) the added text is "fill in at least one of these fields"
   instead of a bare "Required". Inside the trigger this takes over from the plain `_("Required")` that
   `WjsPersonalInfoForm` sets today on its Django-required fields; outside the trigger the form behaves exactly as
   today, including that plain `_("Required")`.
5. **Banner.** A banner at the top of the profile pages lists the journal and the labels still missing, taken
   from the hinted entries that are unsatisfied. It is hidden when nothing hinted is missing.
6. **Tab badges.** In the profile sidebar, each tab with hinted-missing entries shows a count badge (number of
   missing entries, an "any of" group counts 1). Affiliations is a list, not a form: it gets a one-line
   "Required" note above the list when its entry is required.
7. **Stateless.** Banner, badges and missing state are recomputed on every page load; after a save fills a field
   they disappear. The Required help text stays on required fields, filled or not.
8. **No orange/outline styling on fields** (rejected during design). The mockup chosen is "A": banner + tab
   badge, with the field marking done through help text.

## Non-goals / anti-goals

- No change to validation, eligibility, step 4, or the dict's meaning; no per-field styling; no new storage.
- No change for users not coming from submission, and no hints added to S's own default dict (S does not know
  the profile's urls).
- Do not highlight `is_active` or any entry without a `profile` hint.

## Constraints

- S must not import `wjs.jcom_profile`; P already imports from `plugins.wjs_submission`.
- S API addition (the only S change): public helpers to read a journal's entries and test one entry, so P does not
  reach into private functions: `get_required_field_entries(journal) -> list[dict]` and
  `is_required_field_entry_satisfied(user, entry) -> bool`; `missing_required_fields` is rebuilt on them with
  identical behavior (existing tests keep passing).
- The sidebar (`wjs/profile/base.html`) is shared by views that are not `BaseProfileEditView` (newsletters,
  assignment parameters): the sidebar badge and banner go through a template tag that reads `request`
  (session `next`, user, `request.journal`), computed once per request.
- Strings translatable; templates follow `.claude/rules/templates-django.md`; new CSS only for the badge and the
  banner, reusing the existing profile nav classes (`wjs-profile-nav__*`).

## Decisions

User's: highlight only when coming from submission; banner + tab badge (option A) and no field outline; Required
goes in help text following wjs-submission's `required_help_text`; Required stays on regardless of filled state;
highlights disappear once populated after a save.
Mine (check): explicit `profile` hint on dict entries (not derived from paths); "any of" help wording and counting
a group as 1; whitelisted users see nothing; `is_active` never shown; S exposes the two small helpers;
a template tag (not view context) feeds banner/badges.

## Left to the builder

Names of the new module/tag/templates, badge markup and CSS, exact banner wording (translatable, must name the
journal and list the missing labels), test fixtures, where the Affiliations note lives in
`personal_affiliations_edit.html`, how the forms receive the set of required field names (e.g. a form kwarg
filled by `BaseProfileEditView.get_form_kwargs`).

## Amendment (same day, after trying the first implementation)

Direction changed by the user; this section supersedes goals 5 and 6 above where they differ.

- **No field outline and no group box** around alternative ("at least one of") fields: the outline is not used
  anywhere else in the UI. Alternative fields are explained by the help text naming them ("Required - fill in at
  least one of: First name, Last name"; the field the form always requires just says Required).
- **Global banner** (above both columns) is short and lists nothing: it says the profile is incomplete for the
  journal, say it is required by the journal ethical policy, and ask to fill in the tabs highlighted in the sidebar then click "Back to submission". Hidden when nothing hinted is missing.
- **Per-tab banner** under each tab's title (Personal Details, Affiliations, Email, Additional information) lists the
  labels still to fill in on that tab ("Still to fill in on this page: ..."). It is hidden when that tab has nothing
  missing, and a required label can no longer be buried under a scroll. Sidebar count badges stay.
- Everything stays stateless and limited to authors coming from the submission.

## Amendment 2 (2026-11-11, after MR review)

- The professional links entry no longer names its fields: journals show different links, so naming them (in the help
  text, the tab banner and the step 4 label) listed fields the author cannot see. Its label is
  "Additional information (at least one field)" and its fields' help text is "Required - fill in at least one of
  the fields in this page", set through the optional `group_help` key of the `profile` hint. Entries without
  `group_help` (name) keep naming their fields. Eligibility is unchanged: every link path still counts.
