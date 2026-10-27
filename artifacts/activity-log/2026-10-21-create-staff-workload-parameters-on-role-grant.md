## 2026-10-21 — Create StaffWorkloadParameters when a staff role is granted
**What:** New section editors / reviewers / directors (per journal) and new EO members (all journals) now get a
default `StaffWorkloadParameters` row via two receivers in `wjs/jcom_profile/signals.py`, so they are no longer
shown as "Permanently self disabled".
**Why:** wjs/specs#3093 (Bertini in JQuant): `is_enabled_as_editor` returns `False` when no row exists, and nothing
created the row on role grant. Direction from Iacopo's note 74062.
**Decisions:**
- Model defaults only (`workload=0`): clears the "disabled" label; directors still set the workload by hand, so
  the default auto-assignment (`workload__gt=0`) does not pick new editors yet. (User decision.)
- No backfill for existing role holders. (User decision.)
- Triggers: `post_save` on `AccountRole` (created, non-raw) + `m2m_changed` on `Account.groups.through` (EO,
  both sides). The admin saves group membership after the user, hence the m2m receiver.
- Existing tests switched from `create()` to `update_or_create()` instead of adding a way to mute signals.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | builder-check subagent (brainstorming) | ≈74k | ≈2m |
| Review | final whole-branch reviewer (opus) | ≈91k | ≈2m |

**Considered & dropped:** user-save trigger from note 74062 — Django saves the Account at every login
(`last_login`), so it would silently backfill all staff on next login; `JCOMProfile` saves don't reach an
`Account` receiver anyway.
**Follow-ups:**
- Deferred review minors: no `loaddata` guard on the EO m2m receiver; concurrent creation can raise
  `ValidationError` from the model's `full_clean`; no reverse-side non-EO test.
- Local env gotcha: `pytest-freezegun` breaks on py3.13 (`distutils`) → run with `-p no:freezegun`; `-n7`
  exhausts Postgres `max_locks_per_transaction` → use `-n3`/`-n4`.
**Refs:** wjs/specs#3093; spec `artifacts/specs/2026-10-21-staff-workload-parameters-on-role-design.md`; plan
`artifacts/plans/2026-10-21-staff-workload-parameters-on-role.md`.
Eval: 81% — artifacts/evaluations/2026-10-21-staff-workload-parameters-on-role.md
