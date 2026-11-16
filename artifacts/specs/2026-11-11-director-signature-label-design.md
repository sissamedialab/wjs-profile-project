# Same sender signature in "write a message" and "select new editor"; directors sign as "Director"

- **Issue:** wjs/specs#3187 (QA follow-up, notes 81821, 81845, 81863)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-11-11
- **Builds on:** `artifacts/specs/2026-11-03-editor-assignment-signature-role-design.md` (MR !1610, merged)

## Intent

QA found that the "select new editor" signature added by !1610 does not use the "write a message" wording. It
currently shows "JQuant EO", "JQuant Editor", "JQuant past editor" or "JQuant Director". For example, the EO should
sign as "JQuant Editorial Office". The tester also asked that, in the physics journals, every director (main or
not) sign as "<code> Director". JCOM and JCOMAL keep "Editor-in-chief" for the main director and "Deputy Editor"
for the other directors.

## Target behaviour

The same rules apply in "write a message" (`views.py`, `get_initial_body`) and in "select new editor"
(`forms.py`, `SupervisorAssignEditorForm.get_new_editor_message_context` → `{{ sender_role_label }}`). The rules
are checked in this order. The first match wins.

| Sender on the paper | Default (JCAP, JHEP, JINST, JQuant, JSTAT) | JCOM, JCOMAL |
|---|---|---|
| EO (`has_eo_role_by_article`) | Editorial Office | Editorial Office |
| Current editor-in-charge (`is_article_editor`), a director acting as editor included | Editor-in-charge | Editor-in-charge |
| Main director (`has_main_director_role_by_article`) | Director | Editor-in-chief |
| Any other director (`has_director_role_by_article`), a past editor included | Director | Deputy Editor |
| Anyone else | "" (as today) | "" (as today) |

"Write a message" keeps its current layout: "Thank you and best regards," and, for the EO only, the sender's name.
Only the role words change. No other message changes. The developer confirmed with QA that there are no other
signed messages in scope.

## Decisions (by the developer)

1. Physics journals are every journal except JCOM and JCOMAL. The local DB has JCAP, JCOM, JCOMAL, JHEP, JINST,
   JQuant and JSTAT.
2. The difference is a **per-journal setting**, not a hardcoded list of journal codes. Its default means "sign as
   Director", so a new journal needs no configuration.
3. A **data migration** creates the JCOM and JCOMAL overrides. `plugin_settings.install()` writes only the default
   value and never journal overrides.
4. The template variable stays `sender_role_label`. The stored setting texts and migration 0023 stay as they are.
5. English documentation. Work goes on a new branch from `wjs-develop`, because !1610 is merged.

## Design

- **New setting** in the `wjs_review` group: `directors_sign_as_director` (`boolean`, not translatable). The default
  value is true. Pretty name: "Directors sign as Director". Description: "If true, all directors sign their messages
  as 'Director'; otherwise the main director signs as 'Editor-in-chief' and the other directors as 'Deputy
  Editor'." It is created in `set_default_plugin_settings()` like `author_can_contact_director`.
- **One shared helper** returns the role words for a sender on an article, following the table, for example
  `sender_signature_role(articleworkflow, user) -> str` in `communication_utils`. It reads the setting for the
  article's journal. "Write a message" and `get_new_editor_message_context` both call it.
  `get_new_editor_message_context` no longer uses `role_label(role_for_article(...))`.
- **Data migration** `wjs_review/0024_…`. It runs after 0023 and after the setting exists. It creates the setting
  if it is missing, then creates or updates a false override for the journals with code JCOM and JCOMAL, if those
  journals exist. Other journals are untouched. The reverse operation deletes those two overrides.

## Goals

- In every journal, EO and editor-in-charge signatures are identical in both places.
- Directors sign as "Director", except in JCOM and JCOMAL.
- A journal manager can switch the behaviour per journal from the settings UI.

## Non-goals

- Other messages, reminders, or "Dear Editor-in-chief" greetings, which address the director rather than sign.
- Changing `role_for_article` or `role_label`, which other code uses.
- Signatures for special-issue editors or journal admins, which stay empty as today.

## Anti-goals

- Hardcoding journal codes in runtime code. The codes appear only in the data migration.
- Rewriting stored setting texts.

## Calls left to the builder

- The helper's exact name and module. Where the helper is placed must avoid circular imports with `permissions`.
- Whether the migration's create-if-missing step reuses the setting definition or duplicates its minimal fields.
- The value stored for "true" in a Janeway `boolean` setting must match what `get_setting(...).processed_value`
  reads as true.

## Tests

- Helper: each row of the table, for the default and for a journal whose override is false.
- "Select new editor": the initial `note_for_new_editor` contains "<code> Editorial Office",
  "<code> Editor-in-charge" and "<code> Director". The 3 existing form tests in
  `test_editor_assignment_signature.py` are updated to these values.
- "Write a message": the initial body contains the same words. There are existing tests to check and update.
- Migration: it creates the false overrides for JCOM and JCOMAL only, it is idempotent, and its reverse removes them.
