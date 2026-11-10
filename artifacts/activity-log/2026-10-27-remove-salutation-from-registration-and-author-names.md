## 2026-10-27 — Remove salutation from registration and author names
**What:** The registration form no longer asks for a salutation, and a data migration clears all existing salutations and the author-name prefixes copied from them, so author names never render one.
**Why:** specs#3271: the landing page showed "Dr ..." before author names. The leak was not `Account.salutation` itself but Janeway's `snapshot_as_author`, which copies it into `FrozenAuthor.name_prefix` (rendered by `full_name`, including the TeX preambles).
**Decisions:** Checked the production copy first: all 213 non-empty frozen-author prefixes equal the account salutation and no account has a `name_prefix`, so clearing prefixes equal to the salutation is safe. `snapshot_as_author` and Janeway core are left untouched: once the values are cleared it copies nothing, and Janeway's other salutation forms (`QuickUserForm`, `AuthorForm`) are never exposed in the WJS setup (confirmed by the user). Data cleanup is a migration, not a command (user's call). The repo's own registration template had to lose its `form.salutation` line, otherwise the page returned a 500.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming, writing-plans (inline) | not measured | ≈1h of conversation |
| Implementation | native execution, inline | not measured | ≈30m |
| Review | general-purpose reviewer subagent | ≈88k | ≈1m |

**Considered & dropped:** Hiding the field instead of removing it from the form (it could still be posted); patching `snapshot_as_author` or Janeway's forms (needless override of core); a management command for the cleanup.
**Follow-ups:** The migration was only run on test fixtures, not on the production-copy DB; the metadata export still outputs "Dr." as its salutation default for every author.
**Refs:** wjs/specs#3271; spec `artifacts/specs/2026-10-27-remove-salutation-design.md`; plan `artifacts/plans/2026-10-27-remove-salutation.md`; Eval: 80% — artifacts/evaluations/2026-10-27-remove-salutation.md
