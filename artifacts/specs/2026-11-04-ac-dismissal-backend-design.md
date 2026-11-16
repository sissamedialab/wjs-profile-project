# AC dismissal: backend

- **Issue:** wjs/specs#3272 (backend part only; UI design pending in wjs/specs#3367)
- **Functional spec:** wiki [Review/AttentionConditions](https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Review/AttentionConditions) rev. 1.4 (requirement IDs below refer to it)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-11-04

## Intent

Users must be able to dismiss the attention conditions (ACs) they have already handled on a paper, and restore
them. Today only the highest-priority AC is shown, nothing can be set aside, and every re-evaluation of the
ACs would reset any state other than "active".

#3272 is going to be split into a Papers list issue and an article page issue, and the article page UI is
blocked on a design decision (#3367: how to reach dismissed ACs when none is active). This work delivers
everything that does not depend on that decision, so that once the design is settled the frontend work is
layout only:

- the data model and lifecycle of a dismissed AC;
- the write path (toggle) with its permissions;
- the HTMX endpoint, with a minimal functional template so it can be tested end to end;
- the read path: the data the listing and the detail page need, without deciding how it is shown.

## Goals

1. A normal AC can be toggled ACTIVE ↔ DISMISSED by the user it belongs to (AC-1, AC-2).
2. A dismissal survives every re-evaluation of the ACs (nightly `rebuild_attention_conditions`,
   `ACStateEvaluator.evaluate_all()`, the calls from `logic.py`, the unread-messages sync) as long as the
   condition keeps applying.
3. When the condition stops applying, a dismissed AC is resolved like an active one; when it applies again,
   the AC is active again and the old dismissal is not carried over (AC-4).
4. `HAS_UNREAD_MESSAGE` can never be dismissed (AC-3).
5. EO members see each AC once and dismiss it for themselves only.
6. Templates get, per article and per user, the active normal ACs, the dismissed normal ACs and the unread
   flag, both on the listing (for the articles of the current page only) and on the detail page.
7. The toggle endpoint can be exercised by hand on a test environment through a minimal, unstyled panel.

## Non-goals

- The final markup, styling and JS of the Papers list icons/popovers and of the Warning panel (LST-1…LST-6,
  DET-1, DET-2, DET-6, A11Y-*): frontend, after #3367.
- Rewriting the six `lists/elements/*/table_item.html` templates and `lists/elements/attention.html`.
- Dismissing from the Papers list (LST-5), dismissal notes, new AC types, changes to the rules raising ACs.
- Revisiting the EO "Check attention conditions" page beyond showing the new status (gamboz's suggestion
  in #3367 is not decided).

## Anti-goals

- A dismissed AC reappearing as active after the nightly rebuild, or after any message read/unread toggle,
  while its condition still applies.
- A dismissed AC staying dismissed across a resolve → re-trigger cycle.
- One user's dismissal affecting another user, including another EO member.
- Breaking the current listing and detail page before the frontend is rewritten.
- Baking a choice of the pending design into the backend (e.g. hard-coding "panel hidden when only dismissed
  ACs are left").

## Constraints

- Keep the current model: one reused row per `(article, user, code)` (`unique_together`), rows never deleted.
- Follow `CLAUDE.md` / `.claude/rules/`: logic classes are `@dataclass` with `run()` inside
  `transaction.atomic()`; views are thin; hyphenated URL names for new URLs; pytest with the `conftest.py`
  state fixtures.
- The latest migration is `0025_editor_decline_reason_busy_editorial` (on `wjs-develop` as of 2026-11-13).

## Design

### Model and queryset

- `AttentionCondition.Status` gains `DISMISSED = "dismissed"`; migration `0024_…` (choices only).
- `AttentionConditionQuerySet` (`managers.py`) gains:
  - `dismissed()`: `status=DISMISSED`;
  - `displayable()`: `status__in=(ACTIVE, DISMISSED)`.
- `by_user_and_relevance()` orders ACTIVE, then DISMISSED, then RESOLVED, then the rest, so the EO
  "all ACs of an article" page shows the new status in a sensible place. The label comes from the choices.

### Lifecycle in `ac_service.py`

- `upsert_ac()`:
  - no row → create it ACTIVE (as today);
  - existing ACTIVE or RESOLVED (or EXPIRED/SUPPRESSED) row → set it ACTIVE and refresh
    `message`/`priority`/`source` (as today; RESOLVED → ACTIVE is the "new AC" of AC-4);
  - existing DISMISSED row → keep DISMISSED, refresh `message`/`priority`/`source` only.
  - It must stay race-safe as `update_or_create` is today (two concurrent upserts must not raise
    `IntegrityError`).
- `resolve_ac()` and `resolve_all_for_article()` already resolve whatever the status: unchanged.
- `resolve_all_for_user_on_article()`, `resolve_for_role_batch()`, `resolve_ac_batch()` filter on
  `status=ACTIVE` today: they must also resolve DISMISSED rows.
- `rebuild_unread_message_acs()` and the unread sync only touch `HAS_UNREAD_MESSAGE`, which is never
  dismissed: unchanged.
- **What "new AC" means (AC-4).** An AC is new only when its code stopped applying (row RESOLVED) and then
  applied again. A change in what triggers the same code while it keeps applying (e.g. reviewer B becomes
  late before reviewer A's lateness ends) does not reactivate a dismissed row; neither does a changed
  message. No per-trigger fingerprint is stored. Default announced in #3272, pending confirmation.

### Known gaps (out of scope)

Some paths never resolve rows when the condition ends:

- `rebuild_attention_conditions` does not heal ACs left over from a previous state (only time-based ones);
- `resolve_for_role` only reaches users who currently hold the role, so a user who loses a role keeps their
  row; if they get the role back, a DISMISSED row comes back still dismissed.

This already happens with ACTIVE rows, where it is invisible. Auditing every resolve call site in `logic.py`
and `logic__production.py` is left to a separate issue (to be opened); the gap is documented in
`wjs/plugins/wjs_review/CLAUDE.md`.

### EO visibility

`upsert_for_role("eo", …)` writes one row per EO person plus one for the EO system user. `visible_to()`
currently returns both to EO people, so an EO member sees normal ACs twice and their dismissal would leave
the shared copy visible.

- `visible_to(user, obj)`: own rows for everybody. For EO people, the EO system user's rows are added:
  - always for `code=HAS_UNREAD_MESSAGE` (that AC is shared on purpose, see `unread_message_ac_owner`);
  - for any other code, **only when the user has no row of their own with that code on that article**.
- Why the fallback: `visible_to()` treats every EO-group member as EO (`has_eo_role`), while
  `_get_users_for_role("eo")` writes personal rows only for EO-group members with an `AccountRole` on the
  article's journal. Without the fallback, EO users with no such role, and EO members added since the last
  evaluation, would see no normal ACs at all. With it, each AC is still shown once.
- A fallback row belongs to the EO system user, so it is read-only for the viewer: the toggle rejects it
  (it is not theirs), and the read path marks it as not dismissible (e.g. a per-AC flag, or the template
  comparing `ac.user_id` with the viewer) so the template shows no button.
- The EO system user's normal-AC rows keep being written as today; they also stay visible on the EO
  "all ACs" page, which lists every row.
- This is the default announced in #3272 (note 81236), pending Iacopo's confirmation.

### Write path

Logic class `ToggleAttentionConditionDismissal` in `logic.py`:

- input: the AC and the requesting user;
- `run()` in `transaction.atomic()`, re-reading the AC with `select_for_update()`;
- allowed only when the AC belongs to the user, its code is not `HAS_UNREAD_MESSAGE`, and its status is
  ACTIVE or DISMISSED (a RESOLVED AC cannot be toggled); otherwise raises `ValidationError`;
- flips ACTIVE ↔ DISMISSED and saves; no re-evaluation is triggered.

A form (`ToggleAttentionConditionDismissalForm`, like `ToggleMessageReadForm`) whose `save()` runs the logic
class, per `.claude/rules/architecture-django.md`.

### Toggle endpoint

- View `ToggleAttentionConditionDismissalView`, modelled on `ToggleMessageReadView`:
  `HtmxMixin, AuthenticatedUserPassesTest, UpdateView`, POST only; `form_valid` catches `ValidationError`
  and goes to `form_invalid`.
- URL: `status/<int:pk>/attention-conditions/<int:ac_pk>/toggle/`, name `toggle-attention-condition`
  (`pk` is the ArticleWorkflow, as in the existing `wjs-article-attention-conditions` URL; the AC must belong
  to that workflow's article).
- `test_func`: the AC exists on that article and belongs to `request.user`; the other checks are the logic
  class's.
- On success it re-renders the panel template with the fresh read-path data for that article and user.
- An optional `show_all` POST value is echoed back in the context, so the panel can keep the "show all" state
  if the final design keeps that checkbox (DET-9). The view does not otherwise depend on it.
- Whether the panel is shown at all is decided by the template, not by the view, so the pending design can
  change the rule without touching the view.

### Read path

A small value object (e.g. a dataclass `ArticleAttentionConditions`) per (article, user):

- `active`: active normal ACs, ordered by priority then creation;
- `dismissed`: dismissed normal ACs, same order;
- `unread`: the visible `HAS_UNREAD_MESSAGE` AC if active, else `None`;
- convenience booleans the templates need regardless of the design: `has_active` (any active normal AC or
  unread; drives the row highlight, LST-7/8) and `has_any` (anything displayable).

Built by one service function for a single article, and by a batch function for a list of article ids
(one query), both going through queryset methods (`displayable().visible_to(...)` plus a new filter on a set
of articles), never filtering `.objects` directly.

- **Template tag** in `templatetags/wjs_review.py`: given a workflow and a user, returns the
  `ArticleAttentionConditions`; uses the prefetched value attached to the workflow when present, otherwise queries.
- **Listing** (`ArticleWorkflowBaseMixin.get_context_data`): prefetch only for the workflows of the current
  page (today it loads every active AC visible to the user) and attach both the new value and the legacy
  `article_id → message` dict, so the existing `article_requires_attention_tt` filter and templates keep
  working unchanged until the frontend switches over. The legacy dict only contains ACTIVE ACs, so dismissed
  ones disappear from the current UI as a side effect, which is the intended behaviour.
- **Detail page**: the view context (or the tag) provides the same value for the article.

### Minimal panel template

A new template for the panel (the existing `details/sections/attention.html` is left as it is, and the
detail page keeps including it), wrapped in a container with a stable id that the HTMX swap targets:

- active ACs, each with a Dismiss button posting to the toggle URL;
- dismissed ACs, each with a Dismissed button (always listed: no client-side "show all");
- the unread row linking to `wjs_article_messages`, no button;
- nothing when `has_any` is false.

It is plain Bootstrap with no design work. It is included on the detail page **only** for testing the endpoint
— see "Left to the builder" for how.

### Documentation

Update `wjs/plugins/wjs_review/CLAUDE.md`: remove the description of the old
`BaseState.article_requires_attention` dispatcher, describe the materialized AC lifecycle including DISMISSED,
the EO visibility rule and the read-path helpers.

### Tests

In `tests/test_attentionconditions.py` (pytest, state fixtures from `conftest.py`):

- toggle: owner can dismiss and restore; another user, an EO member on someone else's row, and
  `HAS_UNREAD_MESSAGE` are rejected; RESOLVED cannot be toggled;
- a dismissed AC stays dismissed after `rebuild_attention_conditions` and after `evaluate_all()`;
- dismissed → condition ends → resolved → condition applies again → active;
- each of the three batch resolvers resolves DISMISSED rows;
- per-user isolation, including two EO members on the same article; EO members see each normal AC once and
  still see the shared unread AC; an EO member with no personal row sees the system user's row, read-only;
- read path: the value object for a mix of active/dismissed/unread, the batch builder, the listing prefetch
  limited to the current page;
- view: permissions, the rendered panel after toggle, `show_all` echoed.

## Calls I made (to check)

- Toggling a RESOLVED AC is rejected.
- The view never returns an empty response; the template renders nothing when there is nothing to show.
- The legacy filter and templates stay in place; the new data lives alongside them.
- `by_user_and_relevance()` orders DISMISSED between ACTIVE and RESOLVED.
- The three defaults posted in #3272 (EO fallback, code-level AC-4, resolve gaps out of scope) — yours to
  confirm, Iacopo's to overturn.

## Left to the builder

- Exact names of the value object, service functions, tag and template.
- How the testing panel is reachable on the detail page without changing what users see today (e.g. included
  next to the current panel, or behind a query parameter); pick the least invasive option and say which.
- How `upsert_ac` keeps DISMISSED while staying race-safe.
- HTTP status and message when the logic class rejects a toggle.
