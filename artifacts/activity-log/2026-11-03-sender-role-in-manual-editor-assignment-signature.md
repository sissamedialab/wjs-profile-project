## 2026-11-03 — Sender's role in the "EO manually assigns an Editor" signature
**What:** The pre-filled body of the manual editor assignment now signs as "<journal code> <role label>" (e.g. "JCAP EO", "JCAP Director") through a new `sender_role_label` template variable; data migration `wjs_review/0022` rewrites the dead `{{ request.user.role.slug }}` placeholder in every stored copy of `editor_assignment_manual_body`.
**Why:** wjs/specs#3187: `Account` has no `role`, so the placeholder always rendered empty and the signature read just "JCAP" in all journals.
**Decisions:** Role = `role_label(role_for_article(article, request.user))` (EO wins over director; labels from the existing `jcom_profile.constants.LABELS`). Variable named `sender_role_label` (developer's choice: it holds the label, not the slug). Journal overrides are fixed by a data migration instead of a manual DB step, because `plugin_settings.install()` force-updates only the default value, never journal copies (locally JCOM, JCOMAL and JQuant had overrides). The migration matches the placeholder with or without inner spaces, touches every `value_<lang>` column, saves only changed columns, and is reversible. The MR must flag the data migration for reviewer confirmation.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming (inline) | ≈60k | ≈20m |
| Design | superpowers:writing-plans (inline) | ≈25k | ≈8m |
| Implementation | superpowers:executing-plans (inline) | ≈40k | ≈25m (incl. 18m full suite) |
| Review | superpowers:requesting-code-review (Opus subagent) | ≈72k | ≈1m |
| Review | code-eval / doc-sync (inline) | ≈15k | ≈5m |

**Considered & dropped:** A manual DB update listed under the MR's required actions (not testable, easy to miss on some environments); monkey-patching a `role` onto `request.user` so old texts keep working.
**Follow-ups:** Special-issue editors and journal admins who can open the form still get an empty role (`role_for_article` returns ""), outside this issue's scope. `makemigrations --check` reports pre-existing drift on `wjs_review` proxy models, unrelated to this change. Deferred review minors: the migration tests use the live (modeltranslation-patched) registry rather than historical apps; `backwards()` normalises placeholder spacing.
**Refs:** wjs/specs#3187; spec artifacts/specs/2026-11-03-editor-assignment-signature-role-design.md; plan artifacts/plans/2026-11-03-editor-assignment-signature-role.md; Eval: 83% — artifacts/evaluations/2026-11-03-editor-assignment-signature-role.md
