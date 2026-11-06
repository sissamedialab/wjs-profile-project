# Render the sender's role in the "EO manually assigns an Editor" signature

- **Issue:** wjs/specs#3187 ("Setting ignores value in signature")
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-11-03

## Intent

The default text of the `wjs_review` setting `editor_assignment_manual_body` ("Body of EO manually assigns an
Editor") ends with the signature line `{{ article.journal.code }} {{ request.user.role.slug }}`. `Account` has no
`role` attribute, so Django silently renders it as an empty string and the signature reads only "JCAP" (reported on
JCAP, but every journal is affected). The signature must show the sender's role on the paper, e.g. "JCAP EO" or
"JCAP Director".

## Current state

- The setting text is rendered once, as the initial value of `note_for_new_editor` in the editor-assignment form
  (`wjs/plugins/wjs_review/forms.py`, `render_template_from_setting(... "editor_assignment_manual_body" ...)` with
  `context=self.get_new_editor_message_context()`, which today holds only `editor` and `article`). The sender can edit
  the text before sending; what they send is what is stored/notified.
- Stored values: the default (`journal=None`) plus per-journal overrides. In the local DB, JCOMAL, JCOM and JQuant have
  overrides, all containing `{{ request.user.role.slug }}`; production presumably has the same (and JCAP).
- `plugin_settings.install()` force-rewrites the **default** value on install, but never journal overrides.

## Decisions (by the developer)

1. **Role source:** `communication_utils.role_for_article(article, user)` — the slug as it comes from it (`EO` for EO
   staff, `director` for directors, …).
2. **Display:** pass that slug through the existing `jcom_profile.constants.role_label()` so the signature reads
   "EO", "Director", "Editor", … (unmapped slugs come back unchanged).
3. **Existing journal copies:** fixed by a **data migration**, not by a manual DB step. The MR must explicitly flag the
   data migration as a point requiring reviewer confirmation.
4. Documentation language: English.

## Design

- **New context variable `sender_role_label`** in `get_new_editor_message_context()`:
  `role_label(role_for_article(self.instance.article, self.request.user))`. The template text reaches it as
  `{{ sender_role_label }}`.
- **Default setting text** (`plugin_settings.py`, `editor_assignment_manual`): replace
  `{{ request.user.role.slug }}` with `{{ sender_role_label }}`. Nothing else in the text changes.
- **Data migration** `wjs_review/0022_…`: for every `SettingValue` of setting `editor_assignment_manual_body`
  (default and journal overrides, and any translated value fields present), replace the literal substring
  `{{ request.user.role.slug }}` with `{{ sender_role_label }}`. Values without the substring are left byte-for-byte
  untouched; running it again is a no-op. Reverse operation: the inverse replacement (keeps the migration reversible).

## Goals

- The rendered initial body for the manual assignment shows "<journal code> <role label>" for the assigning user.
- All stored copies (default and journal overrides) are corrected without touching any other customised text.

## Non-goals

- Changing any other setting or template (e.g. `{{ request.user.signature|safe }}` elsewhere is unrelated and works).
- Changing `role_for_article` / `role_label` behaviour or adding new labels.
- Altering the subject setting or the message flow after the form.

## Anti-goals

- Overwriting journal-customised text wholesale (only the placeholder may change).
- Making `request.user.role` work by monkey-patching the user/request object.

## Calls left to the builder (marked as such)

- *Builder's call:* exact migration file name and whether it uses `RunPython` with a module-level helper (so the
  replace function can be unit-tested) — preferred.
- *Builder's call:* rare slugs not in `LABELS` (e.g. `past editor` for a director who once edited the paper) are shown
  as-is; acceptable, no special handling.
- Tests: form-level test that the initial `note_for_new_editor` contains "<code> EO" for an EO user and
  "<code> Director" for a director; migration-helper test that it replaces the placeholder in default and override
  values and leaves values without it unchanged.
