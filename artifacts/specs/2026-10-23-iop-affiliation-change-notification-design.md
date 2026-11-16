# Notify the EO when the paper's affiliation changes in a revision (IoP journals)

- **Issue:** wjs/specs#3185
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review` (reads a setting from the sibling `wjs-submission-project`)
- **Date:** 2026-10-23
- **Status:** approved design

## Problem

For IoP journals the affiliation attached to the paper (`ArticleSubmission.affiliation`, chosen in submission
step 4, section "Corresponding author") drives what IoP receives and what access mode / copyright / licence
apply. When an author submits a revision ("Submit revision") or updates metadata ("Update metadata") and the
paper ends up with a different affiliation, the EO must:

- check that the new affiliation matches the "best" affiliation of the corresponding author in the manuscript;
- check the access mode, copyright and licence, and correct the affiliation and/or those if needed.

Today nothing tells the EO that this happened. Once the EO has checked/corrected, its attention is no longer
needed.

## Decision

Send a **system message to the EO** (agreed with the user, following the issue's "alternative" and Matteo's
comment that an unread message acts as an attention condition). No status filter on the paper lists, no new
attention condition, no new workflow state.

## Goals

- When "Submit revision" or "Update metadata" is completed on an IoP journal paper, and the paper's affiliation
  after the revision differs from the one before it, send one message to the EO.
- The message appears in the EO mailbox (email), the paper timeline, the paper messages page and the messages
  overview, and stays **unread for the EO** until someone marks it read (existing `read_by_eo` toggle).
- Subject and body are editable journal settings, like the other EO notifications.

## Non-goals

- Status filter "With affiliation's change" on paper lists (rejected option from the issue).
- Any notification on first submission (there is nothing to compare against).
- Notifications for non-IoP journals.
- Comparing affiliations by organisation/institution (see "Trigger" below).
- Changing how step 4 selects or resets the affiliation.

## Anti-goals

- Re-notifying when the affiliation did **not** change (e.g. author goes through step 4 and keeps the same
  affiliation) — the EO would learn to ignore these messages. Same concern as the #3083 regression for the
  access-mode special request.
- Leaving a message record behind when the revision fails/rolls back. Since the notification runs inside
  `AuthorHandleRevision.run()`'s transaction, the `Message` row rolls back with it. The email, however, is sent
  immediately by `log_operation` and cannot be recalled — the same limitation every other notification in the
  revision flow has (e.g. the step-7 special-request one). *Builder's call (mine):* follow that precedent and
  call directly, no `transaction.on_commit` deferral; fixing this for all revision notifications is out of scope.

## Trigger

Fires when **all** hold:

1. the revision flow is FULL ("Submit revision") or METADATA ("Update metadata") — i.e. flows that include
   step 4. The CONFIRM flow skips step 4 and never fires;
2. the article's journal code is in `plugins.wjs_submission.settings.IOP_JOURNALS` (Django setting
   `SUBMISSION_IOP_JOURNALS`, default `["JCAP"]`; currently unused, reused here);
3. the revision storage carries an `affiliation_pk` (the current code keeps the old affiliation when it is
   falsy, so nothing changes and nothing fires);
4. that `affiliation_pk` differs from the article's current `submission_data.affiliation_id` (which may be
   `None`, e.g. imported papers — `None` → some affiliation counts as a change).

Comparison is by `ControlledAffiliation` primary key. Decided with the user: this deliberately **includes the
case where the corresponding author changed** (step 4 resets the affiliation to the new author's primary one),
accepting that two co-authors at the same institution have distinct affiliation records and will trigger a
message even though the organisation is the same — an extra message the EO marks read is preferred over a
missed one.

## Where it hooks in

`PopulateRevisionStep4.run()` in `wjs_review/logic.py` (called by `AuthorHandleRevision._store_data()` via
`PopulateRevisionSteps`, only for FULL and METADATA flows). During the whole revision all edits live in
`RevisionStorage`; the article still holds the old corresponding author and old affiliation until this method
overwrites them. So:

- capture the old affiliation (and old corresponding author, for the message) **before** the existing
  assignments at the top of `run()`;
- after the affiliation is assigned, if the trigger holds, send the notification.

Precedent for this before/after pattern: `PopulateRevisionStep7.run()` (special-request notification).

## The notification

A small business-logic dataclass in `logic.py`, modelled on `AccessModeSpecialRequestNotification`, holding
the article, old affiliation, old corresponding author (new ones are read from the article). Rendered with
`render_template_from_setting(..., template_is_setting=True)` and a fake request, sent with
`communication_utils.log_operation(...)`:

- recipients: `[get_eo_user(article)]`;
- verbosity: `FULL` (timeline + email) — default;
- `flag_as_read_by_eo=False` — default, so the message is unread for the EO;
- actor: system (`actor=None`) — *builder's call (mine)*: the person submitting the revision may be the owner,
  not the corresponding author, and the message is an automatic alert.

### Settings (new, in `wjs_review/plugin_settings.py`)

Two settings in the `wjs_review` group, created like `add_access_mode_request_notifications()` and registered
in the `csv_writer.write_settings(...)` list:

- `affiliation_changed_in_revision_subject` (text, not translatable). Default:
  `Corresponding author affiliation changed in revision – {{ article.journal.code }}-{{ article.pk }}`
  (must render under the 111-char `Message.subject` limit).
- `affiliation_changed_in_revision_body` (rich-text, not translatable). Default (agreed with the user):

  ```
  Dear EO,
  <br><br>
  the authors of {{ article.journal.code }}-{{ article.pk }} "{{ article.title }}" have changed the corresponding
  author's affiliation while submitting a revision.
  <br><br>
  Previous: {{ old_affiliation|default:"none" }} ({{ old_correspondence_author.full_name }})<br>
  New: {{ new_affiliation }} ({{ article.correspondence_author.full_name }})
  <br><br>
  Please check that the new affiliation matches the corresponding author's affiliation in the manuscript, and
  verify the access mode, copyright and licence. Mark this message as read when done.
  ```

Template context: `article`, `old_affiliation`, `new_affiliation`, `old_correspondence_author`.

## Tests (wjs-profile-project, `wjs_review/tests/test_logic.py`)

Modelled on `test_populate_revision_step7_special_request`, using `review_settings`, `editor_revision`,
`monkeypatch.setattr(<wjs_submission settings>, "IOP_JOURNALS", [journal.code])`:

- IoP journal, same corresponding author, different affiliation → one message to EO, subject/body contain old
  and new affiliation, `read_by_eo` is False, verbosity FULL;
- IoP journal, different corresponding author (affiliation follows) → one message;
- IoP journal, unchanged affiliation → no message;
- IoP journal, old affiliation `None`, new set → one message;
- `affiliation_pk` missing in revision storage → no message, affiliation unchanged;
- non-IoP journal, affiliation changed → no message, affiliation still updated.

## Left to the builder

- Exact name of the notification class and helper methods.
- Whether to call the notification directly from `PopulateRevisionStep4` or via a new `SubmissionEvent`
  (direct call preferred: simpler, no other consumer).
- Per-journal overrides of the setting values (e.g. via `apply_wjs_settings`) — not required.
