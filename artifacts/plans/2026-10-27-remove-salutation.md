# Remove "salutation" from registration and author names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nobody can enter a salutation at registration, and no author name ever renders one; existing salutations are cleared by a data migration.

**Architecture:** Drop `salutation` from `JCOMRegistrationForm`; add a `jcom_profile` data migration that blanks `FrozenAuthor.name_prefix` (where it equals the author's salutation) and then `Account.salutation`; prove with tests that the author-name surfaces never contain a salutation. Janeway core and `snapshot_as_author` are left untouched.

**Tech Stack:** Django 5.2, pytest-django, Janeway core models (`core.Account`, `submission.FrozenAuthor`).

**Spec:** `artifacts/specs/2026-10-27-remove-salutation-design.md` (issue wjs/specs#3271)

## Global Constraints

- Language of everything generated (code comments, commit messages, MR): English.
- Do not modify Janeway core code, forms or templates, nor the admin register form; do not override `snapshot_as_author`.
- Do not touch the XML metadata export (`salutation` there is the frozen author `name_prefix`, default "Dr.").
- Migration order is fixed: clear `FrozenAuthor.name_prefix` first (it needs the salutations still in place), then `Account.salutation`. Reverse is a no-op.
- Only a `name_prefix` equal to the author's salutation is cleared; any other prefix stays.
- Use set-based `update()` calls only (no per-row loops).
- Black line length 119, double quotes, docstrings per `.claude/rules/code-style-python.md`; tests need descriptive assertion messages.
- Migrations are skipped in the test runner: test the migration function by importing the module (pattern in `wjs/plugins/wjs_review/tests/test_attentionconditions.py::test_migration_resolves_stale_appeal_acs`).
- Tests run from `janeway/src` (see `.claude/rules/tests.md`); the repo is imported through the main checkout, so test a worktree commit by switching the main checkout to it detached (`git switch --detach <sha>`) and switch back afterwards.

## Review Focus

- A frozen author with a non-salutation prefix (e.g. "Prof." while the account salutation is "Dr"): must stay untouched by the migration.
- An account with a salutation but no frozen author, and a frozen author with no linked account (`author` is NULL): migration must not fail.
- Running the migration twice: second run is a no-op.
- A frozen author with an empty prefix and an account with an empty salutation (`"" == ""`): must not be touched or error.
- A corporate frozen author (`is_corporate`): `full_name()` returns the corporate name unchanged.

---

### Task 1: Registration form without salutation

**Files:**
- Modify: `wjs/jcom_profile/forms.py` (`JCOMRegistrationForm.Meta.fields`)
- Test: `wjs/jcom_profile/tests/tests.py` (class `TestJCOMWIP`)

**Interfaces:**
- Produces: `JCOMRegistrationForm` without a `salutation` field.

- [ ] **Step 1: Write the failing test** `TestJCOMWIP.test_registration_form_has_no_salutation_field(self, journal)` (with `@pytest.mark.django_db`): build `JCOMRegistrationForm(journal=journal)` and assert `"salutation" not in form.fields`, message "The registration form must not ask for a salutation".
- [ ] **Step 2: Run it and check it fails**
  Run (from `janeway/src`): `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/tests.py::TestJCOMWIP::test_registration_form_has_no_salutation_field`
  Expected: FAIL (field present).
- [ ] **Step 3: Remove `"salutation"` from `JCOMRegistrationForm.Meta.fields`** in `wjs/jcom_profile/forms.py`.
- [ ] **Step 4: Run the test and the other `TestJCOMWIP` tests**
  Expected: PASS.
- [ ] **Step 5: Commit** `fix(jcom_profile): remove salutation from the registration form`

### Task 2: Data migration clearing salutations

**Files:**
- Create: `wjs/jcom_profile/migrations/0045_clear_salutation.py`
- Create: `wjs/jcom_profile/tests/test_clear_salutation_migration.py`

**Interfaces:**
- Consumes: `FrozenAuthor` (`submission` app) with `name_prefix` and `author` (FK to `core.Account`, nullable); `Account.salutation`.
- Produces: `clear_salutations(apps, schema_editor) -> None` in the migration module, importable as `wjs.jcom_profile.migrations.0045_clear_salutation` through `importlib.import_module`.

- [ ] **Step 1: Write the failing tests** in `test_clear_salutation_migration.py` (all `@pytest.mark.django_db`, calling `migration.clear_salutations(django_apps, None)` with `django.apps.apps`; create accounts/frozen authors with the existing `user`/`article` fixtures from the conftests, setting `salutation` and `name_prefix` through `Account.objects.filter(...).update(...)` / `FrozenAuthor.objects.filter(...).update(...)`):
  - `test_migration_clears_account_salutations`: account with salutation "Dr" ends with `salutation == ""`.
  - `test_migration_clears_frozen_author_prefix_equal_to_salutation`: frozen author with `name_prefix="Dr"` whose account has salutation "Dr" ends with `name_prefix == ""`.
  - `test_migration_keeps_frozen_author_prefix_different_from_salutation`: `name_prefix="Prof."`, account salutation "Dr" → prefix stays "Prof." (account salutation still cleared).
  - `test_migration_handles_frozen_author_without_account`: frozen author with `author=None` and `name_prefix="Dr"` is left alone, no error.
  - `test_migration_is_idempotent`: second call changes nothing and does not raise.
  - `test_migration_leaves_empty_prefix_and_salutation_alone`: both `""` → no error, both stay `""`.
- [ ] **Step 2: Run them and check they fail** (module not found).
- [ ] **Step 3: Create `0045_clear_salutation.py`**: dependencies `("jcom_profile", "0044_alter_staffworkloadparameters_brake_on")`, `("core", "0110_one_click_review_on_by_default")`, `("submission", "0089_keyword_created_keyword_deactivated_keyword_notes_and_more")` (re-check the latest `core`/`submission` names in `janeway/src` at implementation time); `clear_salutations(apps, schema_editor)` uses `apps.get_model` and, in this order, `FrozenAuthor.objects.exclude(name_prefix="").filter(author__salutation=F("name_prefix")).update(name_prefix="")` then `Account.objects.exclude(salutation="").update(salutation="")`; `migrations.RunPython(clear_salutations, migrations.RunPython.noop)`. Docstring says why the reverse is a no-op.
- [ ] **Step 4: Run the tests; then `python manage.py makemigrations --check --dry-run` from `janeway/src`** to confirm no model changes are pending.
  Expected: tests PASS; no changes detected for `jcom_profile`.
- [ ] **Step 5: Commit** `fix(jcom_profile): clear existing salutations and the prefixes copied from them`

### Task 3: Author names never render a salutation

**Files:**
- Create: `wjs/jcom_profile/tests/test_salutation_not_rendered.py`

**Interfaces:**
- Consumes: `Account.snapshot_as_author(article)`, `FrozenAuthor.full_name()`, `authors_fullname_comma_and` (`wjs/jcom_profile/templatetags/wjs_tags.py`), `HandleDownloadRevisionFiles.render_latexpreamble(preamble, context)` (`wjs/plugins/wjs_review/logic__production.py`), `clear_salutations` from Task 2.

- [ ] **Step 1: Write the tests** (`@pytest.mark.django_db`; set a salutation "Dr" on the correspondence author's account with `update()`, run `clear_salutations(django_apps, None)`, call `snapshot_as_author(article)` with `force_update=True`):
  - `test_frozen_author_full_name_has_no_salutation`: `frozen_author.name_prefix == ""` and `"Dr" not in frozen_author.full_name()`.
  - `test_authors_listing_has_no_salutation`: `authors_fullname_comma_and(article)` contains no "Dr".
  - `test_latex_preamble_has_no_salutation`: render a JCOM-style preamble snippet (`{% for au in article.frozen_authors %}\author[{{ au.last_name }},~{{ au.first_name|first }}.]{{{ au.full_name|space_to_tilde }}}{% endfor %}`, loading the template library that provides `space_to_tilde`) through `render_latexpreamble` with context `{"article": article}`; assert no "Dr".
  - `test_corporate_frozen_author_full_name_is_unchanged`: a corporate frozen author (`is_corporate=True`, `corporate_name="ACME"`) keeps `full_name() == "ACME"`.
  - `test_status_page_author_block_has_no_salutation`: render `wjs_review/base/elements/author.html` (the status/details page author block) for the frozen author with the context it needs (`workflow`, `frozen_author`, `author`) and assert no "Dr" in the HTML. If the template needs a request, use the `RequestFactory` pattern from existing template tests.
- [ ] **Step 2: Run them.** These should pass straight away once Tasks 1-2 are in (they prove behaviour, they do not drive new code); confirm each fails when the `clear_salutations` call is removed from the test (temporary check), then restore it.
- [ ] **Step 3: Run the whole new test module plus `wjs/jcom_profile/tests/tests.py` and `wjs/plugins/wjs_review/tests/test_metadata_export.py`** to check nothing else depends on salutations.
  Expected: PASS.
- [ ] **Step 4: Commit** `test(jcom_profile): author names never render a salutation`

### Task 4: Lint, full relevant run, changelog fragment check

**Files:**
- Modify: none expected (towncrier is not used by this repo; confirm `ls changes/` and skip if absent).

- [ ] **Step 1: Run `pre-commit run --all-files`** in the worktree. Expected: all hooks pass.
- [ ] **Step 2: Run `pytest --create-db -n7 ../../wjs-profile-project/wjs/jcom_profile` from `janeway/src`** against the final commit (main checkout switched to it detached). Expected: PASS apart from failures that also fail on `origin/wjs-develop`.
- [ ] **Step 3: Switch the main checkout back to its branch** and report the result.

## Self-review notes

- Spec coverage: registration form (Task 1), data migration with ordering/idempotency/safety (Task 2), tests per surface (Task 3: frozen author, listing, preamble, status page; the public article page renders the same `full_name`, covered by the frozen-author and listing tests). Non-goals untouched.
- The public article page itself is not rendered in a test: it needs the full Janeway theme stack; its name comes from `FrozenAuthor.full_name()` and the listing filter, both asserted.
