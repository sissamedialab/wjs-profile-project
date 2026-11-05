# Publisher green light before publication

- **Issue:** wjs/specs#2914 (related: wjs/specs#2678)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-10-13
- **Status:** approved design

## Problem

For JCAP, the publisher (IOP) must confirm that a paper can be published
before the EO starts the publication process. Today nothing in the
production workflow records that confirmation, so the EO can press
"Publish" on a paper IOP has not cleared yet.

## Requirements

- R1. A new production flag `ArticleWorkflow.production_flag_iop_ready`
  (boolean, default `False`) records that the publisher confirmed the paper
  can be published.
- R2. The EO (and only the EO) can toggle the flag at any time during
  production: in `READY_FOR_TYPESETTER`, `TYPESETTER_SELECTED`,
  `PROOFREADING` and `READY_FOR_PUBLICATION`.
- R3. While the flag is not set, publication cannot begin:
  `BeginPublication` refuses to run and the EO is redirected to the article
  status page with the message *"Please ensure that the publisher confirmed
  that this paper can be published (and click on the appropriate action)"*.
- R4. While the flag is not set, the "Publish" action in
  `READY_FOR_PUBLICATION` is shown disabled (greyed out). The server-side
  check of R3 remains the real gate.
- R5. The flag has no other effect: it does **not** take part in
  `ArticleWorkflow.can_be_set_rfp()` nor in any other transition.
- R6. The whole feature applies only to journals listed in the new setting
  `WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION` (default `["JCAP"]`). For
  any other journal the action is not shown, the Publish button is never
  disabled by this flag and `BeginPublication` ignores it.

## Design

### Data model

In `wjs/plugins/wjs_review/models.py`, next to the other production flags:

```python
production_flag_iop_ready = models.BooleanField(
    default=False,
    verbose_name=_("The publisher confirmed that the paper can be published"),
)
```

- One schema migration in `wjs/plugins/wjs_review/migrations/`.
- Added to the production flags listed in `admin.py`.
- `can_be_set_rfp()` is unchanged (R5).
- Existing papers, including those imported from wjapp, start at `False`:
  JCAP papers already in production need an explicit EO confirmation before
  they can be published. The importers are not changed.

### Journal scoping

- New setting in `wjs/defaults/settings.py`, with a comment, following the
  `WJS_JOURNALS_WITH_*` pattern:
  `WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = ["JCAP"]`.
- Two `ArticleWorkflow` methods, next to `can_be_set_rfp()`, are the single
  source of truth used by logic and UI alike:
  - `requires_publisher_confirmation() -> bool`:
    `self.article.journal.code in settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION`;
  - `publisher_confirmation_missing() -> bool`:
    `self.requires_publisher_confirmation() and not self.production_flag_iop_ready`.
- They live on the model (not in `conditions.py`) because
  `logic__production.py` needs them and `conditions.py` already imports
  `logic`, which would create an import cycle.
- `conditions.py` gets a thin wrapper with the `(workflow, user)` signature
  used by action `condition`s:
  `journal_requires_publisher_confirmation(workflow, user)`.

### Set/revoke: logic, view, URL

Modelled on the existing "Mark as Unpublishable" machinery
(`TogglePublishableFlag` / `TogglePublishableFlagView`), with one
difference: the request carries the **requested value** instead of asking
the server to flip the stored one. With a plain toggle, a stale page whose
button reads "Revoke publisher confirmation" would flip an already-revoked
flag back to `True` and open the gate (final code review, Important #1).

- `SetPublisherConfirmedFlag(workflow, confirmed: bool)` (dataclass,
  `logic__production.py`):
  - `_check_conditions()`: state in the four states of R2 **and**
    `workflow.requires_publisher_confirmation()`; otherwise `run()` raises
    `ValueError`;
  - re-reads the stored flag and raises `ValueError` ("already
    recorded" / "already revoked") if it already equals `confirmed`;
  - sets the flag with `save(update_fields=["production_flag_iop_ready"])`
    inside `transaction.atomic()`, so concurrent writes to other production
    flags of the same row (e.g. galley-test results) are not overwritten.
- `SetPublisherConfirmedFlagView` (`views__production.py`): POST only
  (GET → 405), `test_func` = `base_permissions.has_eo_role(user)`; reads
  `?confirmed=1|0` (anything else → error message); redirects to
  `wjs_article_details`, showing `messages.error` on `ValueError`.
- URL `paper_publisher_confirmed/<int:pk>/`, name
  `wjs_set_publisher_confirmed`.

### Action in the four production states

- One `ArticleAction` defined once in `states.py` and added to the
  `article_actions` of `ReadyForTypesetter`, `TypesetterSelected`,
  `Proofreading` and `ReadyForPublication`:
  - `name="toggle publisher confirmed flag"`;
  - `permission=permissions.has_eo_role_by_article`;
  - `condition=conditions.journal_requires_publisher_confirmation` (R6);
  - `is_post=True`;
  - dynamic URL (`custom_get_url`): `?confirmed=1` when the flag is `False`,
    `?confirmed=0` when it is `True`, so the button posts what its label says;
  - dynamic label (`custom_get_label`): *"Publisher confirmed paper is
    publishable"* when the flag is `False`, *"Revoke publisher
    confirmation"* when it is `True`;
  - dynamic confirm text (`custom_get_confirm`) matching the label.
- The "Publish" action of `ReadyForPublication` gets
  `disabled=` a `states.py` callable `(action, workflow, user)` returning
  `workflow.publisher_confirmation_missing()` (R4). The existing
  template already renders `disabled` actions with Bootstrap's `.disabled`
  class, which blocks clicks.
- The label stays "Publish". Because `.disabled` sets `pointer-events: none`,
  a `title` on the button itself never shows: `ArticleButton` gets an
  optional `disabled_cause` (exposed by `as_dict()`), and
  `actions_button.html` wraps a disabled action that declares one in
  `<span class="d-inline-block" tabindex="0" title="…">` (the pattern
  already used by `reviewers_list.html`). Publish declares *"Waiting for the
  publisher confirmation: use the "Publisher confirmed paper is publishable"
  action first."*; actions without a cause render exactly as before.
  wjs-themes does not override these templates (checked).

### Publication gate

`BeginPublication.check_conditions()` gets one extra check after the
existing ones: if `workflow.publisher_confirmation_missing()`, return
`(False, "Please ensure that the publisher confirmed that this paper can be
published (and click on the appropriate action)")`. `BeginPublicationView`
already turns the resulting `ValueError` into a django error message and a
redirect to the status page, as for the missing-issue case.

## Error handling

- Set/revoke in a wrong state / non-JCAP journal, or asking for the value
  already stored (stale page): `ValueError` → error message, flag unchanged.
- Missing or invalid `confirmed` parameter: error message, flag unchanged.
- Request by a non-EO user: 403 from `AuthenticatedUserPassesTest`.
- Publish without confirmation (e.g. stale page or forged POST): rejected by
  `check_conditions()`, nothing changes on the article.

## Testing

In a dedicated `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py`
(existing production fixtures; the journal is opted in/out through
pytest-django's `settings` fixture, since the test journal is `JCOM`):

- Model: `requires_publisher_confirmation()` / `publisher_confirmation_missing()`
  for JCAP with and without the flag, and for JCOM.
- Logic: confirm and revoke in each of the four states; asking for the
  stored value is refused; only the flag column is written; `ValueError`
  in another state (e.g. `ACCEPTED`, `PUBLISHED`) and for a non-JCAP
  journal.
- View: EO can confirm; a stale "revoke" does not confirm; missing/invalid
  `confirmed` is refused; typesetter and author get 403; GET is 405.
- `BeginPublication`: JCAP without the flag is rejected with the message;
  JCAP with the flag passes the check; JCOM without the flag passes the
  check.
- Actions: the toggle action is available to the EO on JCAP and not on
  JCOM; the label and the posted value switch with the flag; the "Publish" action is disabled
  on JCAP without the flag and enabled with it, and never disabled on JCOM.
- Template: a disabled Publish renders inside the hoverable `title` wrapper
  with its cause; an enabled Publish, and a disabled action without a cause,
  render without it.

## Out of scope

- Importer changes (wjapp import scripts).
- Notifications or timeline entries on toggle (the existing toggle has none).
- Showing the flag among the article status badges.
- Any change to the RFP transition.
