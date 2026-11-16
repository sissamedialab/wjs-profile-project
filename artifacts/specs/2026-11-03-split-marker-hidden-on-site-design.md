# Hide the `[...]` split marker when messages are shown on the site

- **Issue:** wjs/specs#3235 ("Check if messages/templates contains placeholders"; direction: note 78717 by
  Iacopo Spalletti, "They are not placeholder but split markers. The rendering issue must be investigated")
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-11-03
- **Status:** approved (2026-11-03)

## Problem

JQuant reported messages on the site showing a literal `[...]`, e.g. "of your typeset Paper as soon as possible
[...]." and "has sent corrections [...] for the Paper 4050."

`[...]` is not a leftover placeholder. It is `Message.SPLIT_MARKER` (`models.py:1612`), written on purpose into
many message settings and reminder templates (e.g. `submission_acknowledgement` in
`jcom_profile/management/commands/apply_wjs_settings.py:180`, `proofreading_request` in `plugin_settings.py:1516`,
`notify_typesetter_proofing_changes` in `apply_wjs_settings.py:593`). It marks where the **email** teaser ends:
for `SYSTEM` messages, `Message.render_message` (`models.py:1846-1851`) keeps only the text before the marker and
appends a "(read more...)" link (`write_message/elements/read_more.html`) to the message page on the site.

The stored body keeps the marker, and the site renders it verbatim (`{{ message.body|safe }}`); no site view ever
learned to hide it (the split was introduced for emails only in commit `4ca913a5`, Aug 2024). The site page is the
"read more" destination, so it must show the full text, without the marker.

The templates are written so that the text reads correctly with the marker removed ("Thank you for submitting
[...] the Addendum …" → "Thank you for submitting the Addendum …").

Side bug: `notification_body, __ = notification_body.split(self.SPLIT_MARKER)` raises `ValueError` when a body
contains the marker more than once.

Local reproduction: `http://jcom.local:8000/plugins/wjs-review-articles/messages/110954/#message-item-3619587`
(message 3619587, JCOM article 89530, `submission_acknowledgement`).

## Requirements

1. Wherever a stored message body is shown on the site, the split marker of a `SYSTEM` message is removed and the
   full text is shown. No replacement text, separator or "expand" control.
2. Non-`SYSTEM` messages (user messages, notes, hijack notices) are shown unchanged: there the marker is not
   special in the email either, and a user may legitimately type `[...]` (e.g. an omission in a quote).
3. The reminder preview (`reminders/elements/reminder.html`) hides the marker too: reminders become `SYSTEM`
   messages (`Reminder.create_message`, `models.py:2601-2610`).
4. The email behaviour does not change: teaser up to the **first** marker, then "(read more...)". A body with
   more than one marker no longer crashes; the teaser stops at the first one.
5. Existing messages already stored with the marker are fixed too (the fix is at display time, the data is not
   touched).

## Non-goals

- Editing the settings/templates that contain the marker, or a data migration on stored messages.
- Changing what the email teaser looks like.
- Other formats/places not rendering a stored body on the site (subjects, exports, admin).

## Design

**Model.** On `Message`, add a property (name left to the builder, e.g. `display_body`) returning `body` with
every `SPLIT_MARKER` occurrence removed when `message_type == SYSTEM`, otherwise `body` unchanged. On `Reminder`,
an equivalent property on `message_body`, always stripping (reminders are always `SYSTEM`). Keep the marker
logic in one place (e.g. a small module-level helper or a `Message` static method reused by `Reminder`).

**Email split.** In `Message.render_message`, split at the first marker only (`partition` or `split(…, 1)`).

**Templates.** Replace the raw body with the new property in:

- `article_messages/elements/message.html:20` (article timeline — the page in the issue)
- `messages_overview/elements/message.html:60`
- `write_message/write_messages.html:126` (original message shown when replying)
- `evaluate_review/review_evaluate.html:71` (editor invite message modal)
- `reminders/elements/reminder.html:25` (reminder preview, with the `Reminder` property)

The builder should grep the plugin (and the `wjs-themes` / other repos in this workspace that override these
templates, if any) for other places rendering `Message.body` / `Reminder.message_body` to users, and apply the
same change there. Note-modal usages in `lists/elements/*/table_item.html` render notes (`NOTE` type), so they are
unaffected by the rule but may use the property for consistency — builder's call.

Whitespace left where the marker was (e.g. a double space) is not cleaned up: HTML collapses it.

## Testing

- Unit tests for the property: `SYSTEM` body with one and with several markers → none left; `USER`/`NOTE` body
  with a marker → unchanged; body without marker → unchanged. Same for the `Reminder` property.
- `render_message` with two markers: no exception, teaser ends at the first marker, "read more" present
  (extend `tests/test_communications.py`, which already covers the email rendering).
- A view test on the article messages page (`wjs_article_messages`) asserting a `SYSTEM` message with the marker
  renders its full text without `[...]`.
