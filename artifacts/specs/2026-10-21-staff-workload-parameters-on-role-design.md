# Create StaffWorkloadParameters when a staff role is granted

- **Issue:** wjs/specs#3093 (implementation direction: note 74062 by Iacopo Spalletti)
- **Project:** wjs-profile-project, `wjs/jcom_profile`
- **Date:** 2026-10-21
- **Status:** approved design

## Problem

When a user is given the section-editor role (e.g. Bertini in JQuant), the
editor lists show them as "Permanently self disabled".

`JCOMProfile.is_enabled_as_editor()` and `is_available_as_editor()`
(`wjs/jcom_profile/models.py`) return `False` when the user has no
`StaffWorkloadParameters` row for the journal. Nothing creates that row when a
role is granted: it only appears when the parameters pages are opened
(`StaffWorkloadParametersUpdate`, `DirectorStaffWorkloadParametersUpdate`), or
through the import / `reset_assignment_parameters` commands.

## Requirements

Note 74062: "Add a signal to create `StaffWorkloadParameters` on user save or
when a new role is added via the manager (a AccountRole is created). We can
restrict the get_or_create if the user has section-editor, reviewer, director,
eo role."

- R1. When an `AccountRole` with role `section-editor`, `reviewer` or
  `director` is created, a `StaffWorkloadParameters` row exists afterwards for
  that user and that role's journal.
- R2. When a user is added to the `EO` group, a row exists for **every**
  journal (EO is cross-journal, as in `reset_assignment_parameters`).
- R3. Rows are created with model defaults (`workload=0`, `enabled=True`, no
  vacancy). Existing rows are never modified.

### Deviation from note 74062: no "user save" trigger

Note 74062 also suggests creating the rows on user save. This is dropped
(user decision). Django saves the `Account` at every login (`last_login`), so
a user-save receiver would create rows for every existing staff user on their
next login: a lazy backfill, which contradicts the "no backfill" decision
below. Saves of the `JCOMProfile` subclass would also not reach a receiver on
`Account` (multi-table inheritance), so the trigger would be patchy anyway.
Rows are created only at the moment a staff role or EO membership is granted.

## Non-goals

- **No default workload.** With `workload=0` the default assignment algorithm
  (`get_available_editor_parameters`, `workload__gt=0`) still skips the user;
  directors set the workload by hand. This issue only clears the
  "disabled" state. (User decision.)
- **No backfill** of existing users that already lack a row. (User decision.)
- No deletion or change of rows when a role is removed or a user leaves EO.
- Roles `editor` and `director-main` are not covered (not in note 74062).

## Design

All code lives in `wjs/jcom_profile/signals.py`, which is already imported by
`JCOMProfileConfig.ready()`. Role slugs and the group name come from
`wjs/jcom_profile/constants.py` (`SECTION_EDITOR_ROLE`, `REVIEWER_ROLE`,
`DIRECTOR_ROLE`, `EO_GROUP`).

Rows are created with `StaffWorkloadParameters.objects.get_or_create(user=...,
journal=...)` and no `defaults` (R3).

Receivers:

1. `post_save` on `core.AccountRole`, only when `created` and the role slug is
   one of the three: `get_or_create` for `instance.user` / `instance.journal`
   (R1). Janeway's `Account.add_account_role()` and the manager role views
   both go through `AccountRole` creation, so this covers them.
2. `m2m_changed` on `Account.groups.through`, `action == "post_add"`, with
   the forward side (`instance` is an Account, `pk_set` holds group ids) and
   the `EO` group among the added groups: `get_or_create` for that user and
   every `Journal` (R2). The reverse side (`group.user_set.add(...)`, where
   `instance` is the Group and `pk_set` holds account ids) is handled the
   same way for each added account when the group is `EO`.

Receiver 1 returns immediately when `raw=True` (fixture loading). The existing
`create_profile_handler` stays unchanged.

`StaffWorkloadParameters.save()` already invalidates the
`StaffWorkloadParameters:{journal}:{user}` cache key, so
`JCOMProfile.parameters()` sees the new row immediately.

Reviewers: `annotate_is_reviewer_available` treats "no row" as available, and
a default row (enabled, no vacancy) is also available, so behaviour for
reviewers does not change.

## Testing

pytest tests in `wjs/jcom_profile/tests/` (new file, e.g.
`test_staff_parameters_signals.py`), real DB, existing fixtures from
`conftest.py`:

- creating an `AccountRole` for each covered role creates the row with
  defaults; `is_enabled_as_editor` becomes `True`;
- creating an `AccountRole` with a non-covered role (e.g. `author`) creates
  nothing;
- an existing row with custom values (workload, `enabled=False`, vacancy) is
  left untouched by role creation and by EO membership;
- adding a user to the `EO` group creates rows for every journal (both
  `user.groups.add(eo)` and `eo.user_set.add(user)`);
- adding a user to a non-EO group creates nothing;
- saving an existing user who has a covered role but no row creates nothing
  (no lazy backfill).

### Existing tests

About 20 existing fixtures/tests grant a covered role or EO membership and
then call `StaffWorkloadParameters.objects.create(user=..., journal=...)` for
the same pair (e.g. `wjs/jcom_profile/tests/conftest.py`,
`wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`,
possibly `test_managers.py`). With the new receivers these would hit
`unique_together`. They are switched to `update_or_create` (or update the
row the signal created), so the suite keeps exercising real behaviour. No
mechanism to disable the signals in tests is added. (Builder's call.)

## Left to the builder

- Exact helper and receiver names.
- Whether to use a bulk query instead of a `get_or_create` loop (journal
  counts are small, a loop is fine).
