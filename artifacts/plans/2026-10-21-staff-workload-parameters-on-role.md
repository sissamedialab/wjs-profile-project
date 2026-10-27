# StaffWorkloadParameters on role grant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a default `StaffWorkloadParameters` row when a user gets the
section-editor/reviewer/director role or joins the EO group, so new staff are
no longer shown as "Permanently self disabled" (wjs/specs#3093).

**Architecture:** Two Django signal receivers in `wjs/jcom_profile/signals.py`
(already loaded by `JCOMProfileConfig.ready()`): `post_save` on
`core.AccountRole` and `m2m_changed` on `Account.groups.through`. Both call
`StaffWorkloadParameters.objects.get_or_create(user=..., journal=...)` with no
defaults. Existing test fixtures that `create()` the same row are switched to
`update_or_create` first.

**Tech Stack:** Django signals, pytest + pytest-django, Janeway `core.models`.

**Spec:** `artifacts/specs/2026-10-21-staff-workload-parameters-on-role-design.md`

## Global Constraints

- Covered roles: `constants.SECTION_EDITOR_ROLE`, `constants.REVIEWER_ROLE`,
  `constants.DIRECTOR_ROLE`; covered group: `constants.EO_GROUP` (all journals).
  `editor` and `director-main` are **not** covered.
- Rows are created with model defaults only (`workload=0`, `enabled=True`, no
  vacancy); existing rows are never modified; nothing is deleted on role removal.
- No receiver on `Account`/`JCOMProfile` save (no lazy backfill). No data migration.
- `create_profile_handler` and `create_special_issue_parameters` stay unchanged.
- Tests run from `janeway/src`: `pytest <path relative to janeway/src>`; never
  run the full suite unless needed (10+ min). Descriptive assert messages.
- Code style: match `signals.py` (module-level receivers with docstrings);
  pre-commit (black/flake8/isort per `.claude/rules/linting.md`), not ruff.
- Documentation language: English.

## Review Focus

1. A role granted twice (`add_account_role` is a `get_or_create`, but a second
   `AccountRole` for another journal) → one row per journal, no IntegrityError.
   Pinned in Task 2 (`test_role_in_two_journals_creates_two_rows`).
2. `user.groups.set([eo_group])` / `add()` of a group the user already has →
   no error, still exactly one row per journal. Pinned in Task 3
   (`test_eo_membership_is_idempotent`).
3. Reverse-side membership `eo_group.user_set.add(user)` → same rows as the
   forward side. Pinned in Task 3 (`test_eo_reverse_add_creates_rows_for_all_journals`).
4. Fixture loading (`loaddata`, `raw=True`) must not create rows. Pinned in
   Task 2 (`test_raw_save_creates_nothing`).
5. A journal created *after* a user joined EO gets no row for that user (by
   design: no trigger fires). Not a bug; documented in the receiver docstring
   (Task 3), no test.

---

### Task 1: Make existing tests tolerant of an auto-created row

**Files:**
- Modify: `wjs/jcom_profile/tests/conftest.py` (lines ≈347, 361, 374, 388, 832, 855, 938)
- Modify: `wjs/jcom_profile/tests/test_views.py` (lines ≈551, 598, 625, 728)
- Modify: `wjs/plugins/wjs_review/tests/test_managers.py` (lines ≈231, 266, 290)
- Modify: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` (lines ≈733, 736, 781, 832, 835, 892, 895, 1074, 1077)

**Interfaces:**
- Produces: no test calls `StaffWorkloadParameters.objects.create(...)` any more
  (`grep -rn "StaffWorkloadParameters.objects.create" wjs` returns nothing).

- [ ] **Step 1: Replace every `StaffWorkloadParameters.objects.create(user=U, journal=J, **values)` in the files above with `StaffWorkloadParameters.objects.update_or_create(user=U, journal=J, defaults={**values})`**

Where the return value is used (`params = ...`, `parameters = ...`), take
element `[0]`. Calls with no extra values become
`update_or_create(user=U, journal=J)[0]` / `get_or_create(...)` equivalently;
use `update_or_create` throughout for uniformity. No other change to the tests.

- [ ] **Step 2: Verify nothing is left**

Run: `grep -rn "StaffWorkloadParameters.objects.create" wjs`
Expected: no output.

- [ ] **Step 3: Run the touched test modules (still without signals)**

Run (from `janeway/src`):
`pytest -n7 ../../wjs-profile-project/wjs/jcom_profile/tests/test_views.py ../../wjs-profile-project/wjs/jcom_profile/tests/test_staff_parameters.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_managers.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
Expected: same pass/fail set as on `wjs-develop` (record any pre-existing failure before Step 1).

- [ ] **Step 4: Commit** (only if per-change commits were chosen)

```bash
git add wjs/jcom_profile/tests wjs/plugins/wjs_review/tests
git commit -m "test: use update_or_create for StaffWorkloadParameters fixtures"
```

---

### Task 2: Receiver on AccountRole creation

**Files:**
- Modify: `wjs/jcom_profile/signals.py`
- Create: `wjs/jcom_profile/tests/test_staff_parameters_signals.py`

**Interfaces:**
- Consumes: fixtures `roles`, `journal`, `journal_factory`, `create_jcom_user`,
  `director_role` from `wjs/jcom_profile/tests/conftest.py`.
- Produces (in `signals.py`):
  - `STAFF_PARAMETERS_ROLES = (constants.SECTION_EDITOR_ROLE, constants.REVIEWER_ROLE, constants.DIRECTOR_ROLE)`
  - `create_staff_parameters_on_role(sender, instance: AccountRole, created: bool, raw: bool = False, **kwargs) -> None`,
    decorated `@receiver(post_save, sender=AccountRole)`.

- [ ] **Step 1: Write the failing tests** in `test_staff_parameters_signals.py`
  (all `@pytest.mark.django_db`; `user = create_jcom_user("staff").janeway_account`)

```python
@pytest.mark.parametrize("role_slug", [SECTION_EDITOR_ROLE, REVIEWER_ROLE, DIRECTOR_ROLE])
def test_covered_role_creates_default_row(create_jcom_user, roles, director_role, journal, role_slug):
    # user.add_account_role(role_slug, journal)
    params = StaffWorkloadParameters.objects.get(user=user, journal=journal)
    assert (params.workload, params.enabled, params.vacancy_start, params.vacancy_end) == (0, True, None, None), "..."
    assert user.jcomprofile.is_enabled_as_editor(journal), "..."

@pytest.mark.parametrize("role_slug", [AUTHOR_ROLE, EDITOR_ROLE, DIRECTOR_MAIN_ROLE])
def test_uncovered_role_creates_nothing(...):
    assert not StaffWorkloadParameters.objects.filter(user=user).exists(), "..."

def test_existing_row_is_not_modified(...):
    # create row workload=7, enabled=False, vacancy_start=date(2026, 1, 1) BEFORE add_account_role(SECTION_EDITOR_ROLE)
    # after: same three values, and StaffWorkloadParameters.objects.filter(user=user).count() == 1

def test_role_in_two_journals_creates_two_rows(..., journal_factory):
    # other = journal_factory("OTHER"); add_account_role(REVIEWER_ROLE, journal) and (REVIEWER_ROLE, other)
    assert set(StaffWorkloadParameters.objects.filter(user=user).values_list("journal", flat=True)) == {journal.pk, other.pk}, "..."

def test_raw_save_creates_nothing(...):
    # AccountRole(user=user, journal=journal, role=Role.objects.get(slug=SECTION_EDITOR_ROLE)).save_base(raw=True)
    assert not StaffWorkloadParameters.objects.filter(user=user).exists(), "..."

def test_user_save_does_not_backfill(...):
    # AccountRole.objects.bulk_create([...SECTION_EDITOR_ROLE...])  (no signal), then user.save() and user.jcomprofile.save()
    assert not StaffWorkloadParameters.objects.filter(user=user).exists(), "..."
```

- [ ] **Step 2: Run to verify the covered-role tests fail**

Run: `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_staff_parameters_signals.py -v`
Expected: `test_covered_role_creates_default_row[*]`, `test_role_in_two_journals_creates_two_rows` FAIL with `DoesNotExist` / set mismatch; the "creates nothing" tests pass.

- [ ] **Step 3: Implement `create_staff_parameters_on_role` in `signals.py`**

Return early unless `created and not raw and instance.role.slug in STAFF_PARAMETERS_ROLES`;
then `StaffWorkloadParameters.objects.get_or_create(user=instance.user, journal=instance.journal)`.
Extend the module docstring with one line on this signal (and on the EO one, Task 3).

- [ ] **Step 4: Run the new tests and the Task 1 modules**

Run: the Step 2 command, then the Task 1 Step 3 command.
Expected: all new tests PASS; Task 1 modules unchanged from their baseline.
If a pre-existing test now fails because it assumed *no* row for a
section-editor/reviewer/director, stop and report it (plan gap), do not
rewrite its assertion silently.

- [ ] **Step 5: Commit** (only if per-change commits were chosen)

```bash
git add wjs/jcom_profile/signals.py wjs/jcom_profile/tests/test_staff_parameters_signals.py
git commit -m "feat: create StaffWorkloadParameters when a staff role is granted"
```

---

### Task 3: Receiver on EO group membership

**Files:**
- Modify: `wjs/jcom_profile/signals.py`
- Modify: `wjs/jcom_profile/tests/test_staff_parameters_signals.py`

**Interfaces:**
- Consumes: Task 2's test module; fixtures `eo_group`, `journal`, `journal_factory`, `create_jcom_user`.
- Produces: `create_staff_parameters_on_eo_membership(sender, instance, action: str, reverse: bool, pk_set: set[int] | None, **kwargs) -> None`,
  decorated `@receiver(m2m_changed, sender=Account.groups.through)`.

- [ ] **Step 1: Write the failing tests** (two journals: `journal` and `journal_factory("OTHER")`)

```python
def test_eo_forward_add_creates_rows_for_all_journals(...):
    # user.groups.add(eo_group)
    assert set(...values_list("journal", flat=True)) == {journal.pk, other.pk}, "..."

def test_eo_reverse_add_creates_rows_for_all_journals(...):
    # eo_group.user_set.add(user)  -> same assertion

def test_eo_membership_is_idempotent(...):
    # user.groups.add(eo_group); user.groups.set([eo_group]); user.groups.add(eo_group)
    assert StaffWorkloadParameters.objects.filter(user=user).count() == 2, "..."

def test_non_eo_group_creates_nothing(...):
    # user.groups.add(Group.objects.create(name="Other"))
    assert not StaffWorkloadParameters.objects.filter(user=user).exists(), "..."

def test_eo_existing_row_is_not_modified(...):
    # row for `journal` with workload=10 before groups.add(eo_group); after: workload still 10, row for `other` has workload 0
```

- [ ] **Step 2: Run to verify the EO-add tests fail**

Run: `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_staff_parameters_signals.py -v -k eo`
Expected: forward/reverse/idempotent/existing-row tests FAIL (no rows); non-EO passes.

- [ ] **Step 3: Implement `create_staff_parameters_on_eo_membership` in `signals.py`**

Only for `action == "post_add"` with a non-empty `pk_set`. Forward side
(`reverse is False`, `instance` is an Account, `pk_set` = group ids): act if the
EO group's pk is in `pk_set`, for `instance`. Reverse side (`instance` is a
`Group`, `pk_set` = account ids): act if `instance.name == EO_GROUP`, for each
account in `pk_set`. "Act" = `get_or_create(user=..., journal=j)` for every
`Journal`. Docstring states that journals created later get no row
(Review Focus 5).

- [ ] **Step 4: Run the new module and the Task 1 modules**

Run: Task 2 Step 2 command, then Task 1 Step 3 command.
Expected: all PASS / unchanged baseline. Same stop rule as Task 2 Step 4 for
tests that assumed no row for EO users.

- [ ] **Step 5: Run the rest of the suite areas that read these rows**

Run: `pytest -n7 ../../wjs-profile-project/wjs/jcom_profile/tests ../../wjs-profile-project/wjs/plugins/wjs_review/tests -m "not fix_labels"`
Expected: no new failures vs. the `wjs-develop` baseline.

- [ ] **Step 6: Lint**

Run (in the repo): `pre-commit run --files wjs/jcom_profile/signals.py wjs/jcom_profile/tests/*.py wjs/plugins/wjs_review/tests/*.py`
Expected: all hooks pass.

- [ ] **Step 7: Commit** (only if per-change commits were chosen)

```bash
git add wjs/jcom_profile/signals.py wjs/jcom_profile/tests/test_staff_parameters_signals.py
git commit -m "feat: create StaffWorkloadParameters when a user joins the EO group"
```
