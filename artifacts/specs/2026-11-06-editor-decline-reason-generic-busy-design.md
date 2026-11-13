# Generic "busy" decline reason in the Editor's "Decline assignment" modal

- **Issue:** wjs/specs#3306 ("Rename entry in Editor's \"Decline assignment\" modal")
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Size:** small change
- **Documentation language:** English

## Intent

The Editor's "Decline assignment" modal offers the reason "Already too busy with JCOM
editorial work". With more journals live (JCAP), the JCOM reference is wrong everywhere
but JCOM. It was agreed to use a single generic reason for all journals, JCOM included:
"Too busy with other editorial tasks".

## Goals

- On every journal, the modal shows "Too busy with other editorial tasks" in place of
  "Already too busy with JCOM editorial work"; no entry mentions JCOM.
- The Director's decline notification (`{{ decline_reason }}` in the
  `editor_decline_assignment_default` setting) shows the new text when this reason is
  selected.
- Existing `PastEditorAssignment` rows declined with the old reason are stored with the
  new one.

## Non-goals

- Other decline reasons are unchanged (`BUSY` "Too busy in general" stays as is).
- Director notifications already sent keep their original text.
- `import_wjapp` storing `DeclineReasons.BUSY.label` instead of the code in
  `decline_reason` is a separate oddity, not touched here.

## Design

- `PastEditorAssignment.DeclineReasons` (`models.py`): remove `JCOM_BUSY`, add
  `BUSY_EDITORIAL = "BUSY_EDITORIAL", _("Too busy with other editorial tasks")` in the
  same position (second entry, right after `EMPTY`), so the modal order is unchanged.
- `EditorDeclinesAssignmentForm` and `HandleEditorDeclinesAssignment._get_message_context`
  read `DeclineReasons.choices`, so they pick up the new code/label with no logic change.
- One new `wjs_review` migration:
  - `AlterField` on `PastEditorAssignment.decline_reason` with the new choices;
  - `RunPython` moving `decline_reason="JCOM_BUSY"` → `"BUSY_EDITORIAL"`, with a reverse
    function moving `"BUSY_EDITORIAL"` → `"JCOM_BUSY"`.
- Translation catalogs (`wjs/locale/{en,es,pt}/LC_MESSAGES/django.po`): the old msgid is
  replaced by the new one, following the repo's usual catalog workflow.

## Testing (TDD)

- Form choices contain `BUSY_EDITORIAL` with the new label and no label mentions "JCOM".
- Declining with `BUSY_EDITORIAL` stores the code on `PastEditorAssignment` and the
  Director notification context/body carries "Too busy with other editorial tasks".
- Migration test: a row with `JCOM_BUSY` becomes `BUSY_EDITORIAL` forward, and back on
  reverse; other reasons are untouched.

## Verification outside the code

- Run the migration on the test environment data and confirm no `JCOM_BUSY` rows remain;
  report it in a comment on the issue (per the issue's technical notes).
- Functional steps from the issue (JCAP and JCOM) are run by the developer on a test
  environment.

## Left to the builder

- Exact migration file name and the test module(s) hosting the new tests.
