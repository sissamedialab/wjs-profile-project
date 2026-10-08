# Design: editor view permissions on appeal

**Issue:** [specs#2903 — Update editor view permissions on appeal](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2903)
(Journal: JCAP, Priority 1, Size 2d, Sprint 26W38)

**Functional requirements:** wiki page
[Review/EditorArticleInformation — Article information display permissions for Editors](https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Review/EditorArticleInformation)
(rev. 1.0, 2026-06-24). Only the items marked **bold** in that page are new behaviour; everything
else describes what already happens and is pinned by tests, not re-implemented.

**Status:** design approved in conversation 2026-09-26, pending written-spec review.

## Context

When the EO opens an appeal on a rejected article (`logic.OpenAppeal`), it may hand the article to a
different editor. From then on four kinds of editor can look at the same article, and each must see
a different slice of it. The functional spec names them (glossary):

| Editor type | Definition (functional spec) |
| --- | --- |
| Past editor | Has a `PastEditorAssignment` for the article and no current `WjsEditorAssignment` |
| Removed editor for appeal | Has a `PastEditorAssignment` for the article flagged `on_appeal` |
| Assigned editor | Current editor; the article has no `OPEN_APPEAL` `EditorDecision` in any round |
| Appeal editor | Current editor; the article has an `OPEN_APPEAL` `EditorDecision`, no `REJECT` `EditorDecision` is linked to them, and their assignment was in force when the (latest) appeal was opened, or the appeal is still unresolved (no later decision) |

### What the code does today (relevant facts)

- `PastEditorAssignment` (`models.py`) is created in one place only,
  `BaseDeassignEditor._delete_assignment` (`logic.py`), and has **no** appeal flag.
  `SupervisorChangeEditorAssignment` has an `appeal: bool` field, but uses it only to skip
  `_log_past_editor()` and to forward it to `AssignToEditor`; it never reaches
  `BaseDeassignEditor`. The rounds the removed editor handled are copied into
  `PastEditorAssignment.review_rounds`, but no visibility code reads that relation.
- `OpenAppeal.run()`:
  1. `update_editor()` → `SupervisorChangeEditorAssignment(..., appeal=True)` when the editor
     changes; `BaseAssignToEditor._assign_editor` links the **new** `WjsEditorAssignment` to the
     current, already-rejected review round.
  2. `_handle_decision()` → `HandleDecision` creates the `OPEN_APPEAL` `EditorDecision` on that same
     rejected round, with `editor = request.user` (the EO).
  3. `_log_author()` → logs "Appeal granted" (`eo_opens_appeal_subject` / `_body` settings) with
     `actor=self.new_editor`, recipient the correspondence author.
- `ArticleWorkflow.get_review_versions` grants a round when the user passes `PermissionChecker` on
  any `EditorDecision` of the round, is the author, passes on any review assignment, or passes on the
  round's `WjsEditorAssignment`. Because the appeal editor's assignment is linked to the rejected
  round, the appeal editor currently sees that round.
- `EditorPermissionChecker.check_default` (`logic__visibility.py`) shows `EditorDecision`,
  `EditorRevisionRequest` and review assignments only when `instance.editor == user`; `Article` /
  `ArticleWorkflow` are visible to current and past editors. Custom `PermissionAssignment` rows are
  evaluated before the defaults. On reassignment, `_migrate_review_assignments` already grants the
  old editor explicit `ALL` on completed review assignments.
- `get_messages_related_to_me` (`communication_utils.py`) shows an editor the messages they sent,
  received, or that have no recipients; `HIJACK` messages are already excluded for everybody.
  Deselection messages (`_log_past_editor`) go only to the deselected editor, and are not sent at
  all when `appeal=True`.
- On the article page the status badge (`details/sections/title.html`) is already shown only to
  article managers and authors, so past editors never see it. The listing
  (`lists/elements/editor/table_item.html`) shows status, reviewers and issue to anyone who reaches
  the row; past editors reach it through `EditorArchived`.
- `WithdrawPreprint` already distinguishes withdrawal after a rejection
  (`author_or_owner_withdraws_preprint_after_a_rejection`), but the text prefilled in the author's
  notification form comes from the same `author_withdraws_preprint_subject` / `_body` settings in
  both cases.

## Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Editor type is computed by a plain function `get_editor_type(user, article)` in `wjs_review/permissions.py`, **not** by a `JCOMProfile` method as the issue text suggests. | It describes a user↔article relationship, sits naturally next to `is_past_article_editor` and friends, takes a plain `Account`, and avoids a `jcom_profile` → `wjs_review` circular import. Deviation noted in the MR. |
| D2 | The editor who rejected and is re-assigned to the appeal is an **Assigned editor**. | The glossary leaves the case undefined; they already know the rejected round and the appeal. |
| D3 | Past editors (both kinds) see every round in their `PastEditorAssignment.review_rounds`, with editor report, reviewer reports, version files and cover letter; explicit custom `DENY` still wins. | "All Editor assignment linked to the user" can only mean the recorded past assignment, since the live one is deleted on deassignment. |
| D4 | The appeal editor loses the rejected round by a special case in `get_review_versions` that forces `has_permission=False` for any round holding an `OPEN_APPEAL` decision (not by a custom DENY permission). | The functional spec's Notes call this the more limited, safer option. Skipping only the `WjsEditorAssignment` branch is not enough: on an editor change `_migrate_review_assignments` moves the rejected round's review assignments to the appeal editor, who would still reach the round through them. |
| D5 | "Appeal granted" is logged **without an actor** (hence by the system user); existing messages are fixed by a data migration. No new filter in `get_messages_related_to_me`. | The editor is then neither sender nor recipient, so the existing rules hide it. |
| D6 | The withdraw-after-appeal notification gets its own settings; default text copied from the current one until MT provides the wording. | Functional spec: "content different from first withdrawn, add a custom setting". |

## Design

### 1. Editor type

**Model — `PastEditorAssignment.on_appeal`**

```python
on_appeal = models.BooleanField(_("Removed on appeal"), default=False)
```

Schema migration `wjs_review/0019_pasteditorassignment_on_appeal.py` (it was `0018` when written;
renumbered after rebasing on `wjs-develop`, whose latest is `0018_resolve_stale_appeal_acs`).

**Propagation**

- `BaseDeassignEditor` gains the dataclass field `appeal: bool = False`; `_delete_assignment` sets
  `on_appeal=self.appeal` on the `PastEditorAssignment` it creates.
- `SupervisorChangeEditorAssignment._deassign_current_editor` passes `appeal=self.appeal`.
- `HandleEditorDeclinesAssignment` (positional call) and the import scripts are unaffected; method
  names monkeypatched by import scripts (`_log_past_editor`) are not renamed.

**Function — `permissions.get_editor_type`**

```python
class EditorType(models.TextChoices):
    ASSIGNED = "assigned", _("Assigned editor")
    APPEAL = "appeal", _("Appeal editor")
    PAST = "past", _("Past editor")
    REMOVED_FOR_APPEAL = "removed_for_appeal", _("Removed editor for appeal")


def get_editor_type(user: Account, article: Article) -> EditorType | None:
    ...
```

Resolution order:

0. A user without an editor role on the journal gets `None`, whatever their assignments (same
   definition as `permissions.is_article_editor` / `is_past_article_editor`).
1. User is the editor of the current `WjsEditorAssignment`:
   - article has an `OPEN_APPEAL` `EditorDecision` (any round), **and** no `REJECT` decision has
     `editor=user`, **and** either the user's current assignment was made at or before the latest
     `OPEN_APPEAL` was recorded (`OpenAppeal` assigns the new editor first, then records the decision)
     or the appeal is unresolved (no decision recorded after it, e.g. an editor swapped in mid-appeal)
     → `APPEAL`;
   - otherwise → `ASSIGNED` (covers D2, and editors assigned by plain reassignment after the appeal
     was resolved: without the scoping, the appeal classification would never expire).
2. User has a `PastEditorAssignment` on the article (looked up with `PastEditorAssignment.objects.for_editor`)
   **and** still has an editor role on the journal (same definition as `permissions.is_past_article_editor`):
   - any of them has `on_appeal=True` → `REMOVED_FOR_APPEAL`;
   - otherwise → `PAST`.
3. Otherwise → `None`.

A current assignment always wins over past ones (same precedence as `role_cache`), so an editor
removed and later re-assigned is treated by their current role.

**Template filters** (`templatetags/wjs_review.py`) — boolean filters built on `get_editor_type`, so
templates never compare string literals: `is_user_former_article_editor` (type is `PAST` or
`REMOVED_FOR_APPEAL`) and `hide_last_submitted` (see §2).

### 2. What each type sees

**Article listing** (`lists/elements/editor/table_item.html`) — for `PAST` / `REMOVED_FOR_APPEAL`:

- status cell: fixed, translated label "Unassigned" instead of `table_item_status.html`;
- reviewers cell: empty;
- issue cell: empty.

`APPEAL` / `ASSIGNED` rows are unchanged.

**Article page**

- Status badge: already hidden for past editors — pinned by a test, no change.
- "Last submitted" (`details/elements/metadata_main.html`): hidden for `PAST` /
  `REMOVED_FOR_APPEAL` when `article|get_version_submission_date` is later than the user's latest
  `PastEditorAssignment.date_unassigned`. The comparison lives in the `hide_last_submitted(workflow,
  user)` filter, not inline template logic.

**Review versions — past editors (D3)**

- `get_review_versions`: a round is granted when it is in the `review_rounds` of any
  `PastEditorAssignment` of the user for the article (in addition to the existing checks).
- `EditorPermissionChecker.check_default`: for a user with a `PastEditorAssignment` on the article
  (the "past rounds" are the union of their `review_rounds`), additionally return `True` for:
  - an `EditorDecision` whose `review_round` is a past round (editor report);
  - an `EditorRevisionRequest` whose `review_round` is a past round (version files and editor
    report of that revision), and — for the secondary permission only (author cover letter) — one
    whose round number is one less than a past round's, because a version's cover letter lives on the
    previous round's revision request (`ReviewVersion.cover_letter`);
  - a completed review assignment (`date_complete` set) whose `review_round` is a past round
    (reviewer report). Incomplete ones moved to the new editor stay hidden, consistent with
    `_migrate_review_assignments`.

  Custom `PermissionAssignment` rows are still evaluated first, so an explicit `DENY` keeps an item
  hidden.
- Result in `details/elements/review_version.html`: editor report (`version.final_decision`),
  reviewer reports, version files (`version.file_container`) and cover letter become visible for
  those rounds. The template conditions are verified (and adjusted only where they bypass the
  permission checker).

**Review versions — appeal editor (D4)**

In `get_review_versions`, when `get_editor_type(user, article) == EditorType.APPEAL`, rounds that
have an `OPEN_APPEAL` `EditorDecision` are skipped entirely (`has_permission=False`, as in the
functional spec's Notes). Only skipping the `WjsEditorAssignment` branch would not do: the rejected
round's review assignments were moved to the appeal editor by `_migrate_review_assignments`. Rounds
after the appeal are unaffected. While the article is still `UNDER_APPEAL` the rejected round is the
latest one, so the appeal editor sees only the initial "fake" version (current article files).

*Guidance for future changes.* The skip is a hard-coded round-level `continue`, applied before (and
independently of) `PermissionChecker`: no `PermissionAssignment`, not even an explicit ALLOW, can
re-grant an appealed round to an appeal editor. This is a deliberate choice (D4), not an oversight:
the functional spec asks for the limited, safe behaviour, and a DENY permission would not be enough
because of the migrated review assignments. The `latest` flag of the returned versions counts only
the versions actually appended, so skipped rounds do not shift it. If a requirement ever needs the
rule to be configurable (per journal/user), rework it through the permission machinery (e.g. a
default DENY `PermissionAssignment` for appeal editors on appealed rounds, plus handling of the
migrated review assignments) rather than adding more special cases to `get_review_versions`.

**Timeline** — `get_messages_related_to_me` is unchanged: hijack is already excluded, deselection
messages already reach only the deselected editor (and are not sent on appeal), and "Appeal granted"
is handled by §3. Covered by tests per editor type.

### 3. Notifications and data

**"Appeal granted" sender (D5)**

- `OpenAppeal._log_author` stops passing `actor=self.new_editor`; `log_operation` then uses
  `get_system_user(journal)` and does not notify an actor. Recipient (correspondence author) and
  `flag_as_read_by_eo=True` are unchanged.
- Data migration `wjs_review/0020_appeal_granted_system_actor.py`: for articles with an
  `OPEN_APPEAL` `EditorDecision`, find messages targeting the article whose recipients include the
  correspondence author and whose subject equals the journal's rendered `eo_opens_appeal_subject`
  (default and journal overrides, checked against production values before writing the
  migration), and set their `actor` to the journal's system user. Reverse: no-op.

**Withdraw after appeal (D6)**

- New settings in `plugin_settings.py`: `author_withdraws_preprint_after_appeal_subject` and
  `author_withdraws_preprint_after_appeal_body` (group `wjs_review`), defaults copied from
  `author_withdraws_preprint_subject` / `_body` and marked in the MR as awaiting MT's wording.
- Wherever the author's withdraw form is prefilled from the existing settings, use the new ones
  when the article has a past `REJECT` decision (same predicate as
  `WithdrawPreprint._has_past_rejection`).
- Recipient is unchanged (current editor, otherwise EO), so the appeal/assigned editor gets it and
  a removed-for-appeal editor does not.

**Removed-for-appeal notifications** — open-appeal, deassignment and withdraw messages are already
not sent to them; pinned by tests.

## Testing

TDD in `wjs/plugins/wjs_review/tests/`. Appeal scenarios use `rejected_article` + `OpenAppeal` with a
**different** appeal editor (as `test_logic.py::test_open_appeal` does); the broken
`appeal_submitted_article` fixture (depends on a non-existent `open_appeal_article`) is fixed.

- `get_editor_type`: each type, same-editor appeal (→ `ASSIGNED`), non-editor (→ `None`),
  re-assigned past editor (→ current type).
- `on_appeal` propagation: `OpenAppeal` with a new editor sets it; plain `SupervisorChangeEditorAssignment`
  and editor decline leave it `False`.
- `get_review_versions` / element visibility per type, including a custom `DENY` for a past editor.
- Listing row and "Last submitted" rendering per type.
- "Appeal granted" actor is the system user and is absent from the appeal editor's timeline;
  data migration updates matching messages only.
- Withdraw after appeal uses the new settings; removed-for-appeal editor receives nothing.

## Out of scope

- Changing `get_messages_related_to_me` filters (not needed, see §2).
- Caching the editor type in `role_cache`.
- Final wording of the withdraw-after-appeal message (MT).
