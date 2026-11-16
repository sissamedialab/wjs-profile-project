## 2026-11-11 — Same signature in "write a message" and "select new editor"; directors sign as "Director"
**What:** "Write a message" and "select new editor" now take the sender's role words from one helper,
`communication_utils.sender_signature_role()`. The words are Editorial Office, then Editor-in-charge, then the director
wording, else nothing. Directors sign as "Director" unless the new `wjs_review` setting `directors_sign_as_director`
is false for the journal. Migration 0024 sets it to false for JCOM and JCOMAL, which keep "Editor-in-chief" and
"Deputy Editor".
**Why:** QA on wjs/specs#3187 (notes 81821, 81863). The fix from 2026-11-03 used `role_label(role_for_article())`,
which gave "EO", "Editor" and "past editor" instead of the "write a message" wording. The tester then asked that every
director sign as "Director" in the physics journals: JCAP, JHEP, JINST, JQuant and JSTAT, meaning all journals except
JCOM and JCOMAL. The developer confirmed the scope: only these two places, and a director who is the editor-in-charge
signs as "Editor-in-charge".
**Decisions:**
- The difference between journals is a per-journal boolean setting, true by default, so new journals sign as
  "Director" with no configuration. It is not a hardcoded list of journal codes.
- The JCOM and JCOMAL overrides come from a data migration, not a manual step, because `install()` never writes
  overrides.
- 0024 also creates the setting and its default value `"on"`. Without the default, `get_setting` raised
  `SettingValue.DoesNotExist` in every other journal until `install()` ran; this was seen on the local DB.
- `sender_role_label` keeps its name, so the stored texts and migration 0023 stay as they are.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:writing-plans (inline) | ≈30k | ≈10m |
| Implementation | superpowers:executing-plans (inline) | ≈60k | ≈45m (incl. 24m full suite) |
| Review | superpowers:requesting-code-review (Opus subagent) | ≈100k | ≈4m |
| Review | code-eval (inline) | ≈5k | ≈2m |

**Considered & dropped:**
- A hardcoded `{"JCOM", "JCOMAL"}` list: every journal change would need a code change.
- Two text settings holding the actual words: more flexible, but more to configure and migrate.
- Leaving the overrides to be set by hand in each environment: a step someone can forget.
**Follow-ups:**
- Deferred review minors:
  - 0024 writes every `value_<lang>` column, while the UI writes only the active language. This only matters with a
    non-English UI language.
  - 0024 has no explicit dependency on the `journal` app.
  - 0024 duplicates the setting's pretty name and description. An existing bug in `create_customization_setting`
    (`force` patches the SettingValue instead of the Setting) means `install()` can't update that `Setting` row.
- The test EO user's full name is "JCOM Editorial Office". Substring checks on signatures can pass by accident, so
  assert on the last line.
- The developer will handle all replies to QA; do not post on the issue.
**Refs:** wjs/specs#3187; follows the 2026-11-03 entry (MR !1610). Spec: artifacts/specs/2026-11-11-director-signature-label-design.md;
plan: artifacts/plans/2026-11-11-director-signature-label.md. Eval: 83% — artifacts/evaluations/2026-11-11-director-signature-label.md
