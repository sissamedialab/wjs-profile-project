# Timeline message for "Accepted → Ready for typesetter"

- **Issue:** wjs/specs#3178 (related to the JCAP go-live feedback wjs/specs#3162)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-10-06
- **Status:** approved design

## Problem

The transition of a paper from `Accepted` to `Ready for typesetter`
(`ArticleWorkflow.system_verifies_production_requirements`) leaves no trace in
the article timeline. It happens in two places:

1. **Automatic** — `VerifyProductionRequirements.run()` (called by the
   `perform_checks_at_acceptance` handler right after acceptance): when all the
   journal's `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` pass, the transition is
   run by the system.
2. **Manual** — `ConfirmProductionReadiness.run()`: the EO confirms production
   readiness from the article page for papers held in `Accepted` (e.g. JCAP TA
   papers, see `jcap_ta_not_yet_confirmed`).

## Design

- A single helper in `logic__production.py` logs the operation via
  `communication_utils.log_operation()`; both code paths call it inside their
  existing `transaction.atomic()` block, right after the FSM transition is saved.
- Message properties (modelled on `ProductionComplete._log_operation`):
  - `verbosity=Message.MessageVerbosity.TIMELINE` (no email);
  - `actor`: the user passed by the action (manual) or `None` → system user (automatic);
  - `recipients=[get_eo_user(article)]`, `flag_as_read=True`, `flag_as_read_by_eo=True`
    (purely informational, it must not raise unread-message ACs).
- Subject and body come from two new `wjs_review` customization settings,
  declared in `plugin_settings.py` following the `jcap_ta_pending_notification`
  pattern and added to the CSV export list:
  - `ready_for_typesetter_subject` (text);
  - `ready_for_typesetter_body` (rich-text).
  Both are rendered with `render_template` and a context
  `{"article": article, "actor": <Account or None>}`, so the default
  body can distinguish the manual confirmation from the automatic verification.
- The helper only logs and performs no permission/role check: authorization
  stays in the action (`_check_conditions` / FSM permission), which runs before
  the helper is called.
- When the checks fail (paper held in `Accepted`) nothing new is logged: the
  existing `_log_acceptance_issues` / JCAP TA pending messages already cover it.

## Out of scope

- No data migration / backfill of timeline messages for papers that already
  went through the transition.
- No email notification to anybody.

## Testing (TDD)

- Automatic path: accepting a paper whose checks pass creates exactly one
  TIMELINE message with the configured subject, actor = system user.
- Manual path: `ConfirmProductionReadiness` creates exactly one TIMELINE message,
  actor = the confirming user, body mentions the confirmation.
- Held in `Accepted` (checks fail): no "ready for typesetter" message.
- The message is read for the EO and does not create a `HAS_UNREAD_MESSAGE` AC.

Settings are available in tests through `set_default_plugin_settings(force=True)`
in `wjs/plugins/wjs_review/tests/conftest.py`. On deploy they are installed
automatically (`run_customizations` → `link_plugins` → `install_plugins wjs_review`
→ `set_default_plugin_settings(force=True)`); on hand-built instances run
`setup_review_settings`, otherwise `get_setting` raises and the transition rolls back.
