# Resend production zip to the publisher: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the EO resend a production export zip whose first send failed, from the article page, with the same bookkeeping as the first send.

**Architecture:** A new dataclass logic class `ResendProductionXMLToPublisher` wraps the existing `SendProductionXMLToPublisher` (composition). A POST-only EO view runs it. A shared `ArticleAction`, visible only while the `PRODUCTION_EXPORT_SEND_FAILED` attention condition still applies, exposes it in the production states, next to `PUBLISHER_CONFIRMED_ACTION`.

**Tech Stack:** Django, django-fsm, pytest (plugin `wjs_review`, run from `janeway-5.2/src` with `PYTHONPATH=<worktree>`).

**Spec:** `artifacts/specs/2026-11-19-resend-production-zip-design.md`

## Global Constraints

- Failure of the first send stays non-blocking: `SendProductionXMLToPublisher.run()` keeps swallowing `SFTPSendError` and flagging the attention condition for its two existing callers.
- The resend goes through `SendProductionXMLToPublisher`; no second code path builds or sends the zip.
- New business logic is a dataclass with `run()` in a transaction; guards raise `ValidationError`; views catch `(ValueError, ValidationError)`.
- New URL path and name use hyphens.
- Action visibility goes through `states.py` (`ArticleAction` with `permission=` and `condition=`), never templates.
- No migration. English everywhere. Line length 119, double quotes, docstrings per `.claude/rules/code-style-python.md`.

## Review Focus

- A failed resend must keep the refreshed attention condition (message of the new error): the `ValidationError` must be raised after the transaction that upserted it has committed, otherwise the rollback erases it.
- The EO dismissed the attention condition: the action still shows and the resend still works (the condition still applies); treat ACTIVE and DISMISSED as "applies".
- Double click or two EOs: the second POST finds the condition resolved, raises `ValidationError`, and does not send again.
- Journal with no send function but a leftover attention condition: `ValidationError`, nothing sent.
- A non-EO user (typesetter, author, anonymous) POSTing to the URL is refused and nothing is sent.

---

### Task 1: "Condition still applies" query and predicate

**Files:**
- Modify: `wjs/plugins/wjs_review/managers.py` (`AttentionConditionQuerySet`), `wjs/plugins/wjs_review/ac_service.py`, `wjs/plugins/wjs_review/conditions.py`
- Test: `wjs/plugins/wjs_review/tests/test_resend_production_zip.py` (new)

**Interfaces:**
- Produces: `AttentionConditionQuerySet.for_code(code: str)`; `ac_service.production_export_send_failed(article: Article) -> bool`; `conditions.production_export_send_failed(workflow: ArticleWorkflow, user: Account) -> bool`.

- [ ] **Step 1: Write failing tests** in the new file, using `accepted_article` and `ac_service.upsert_for_role(..., "eo", PRODUCTION_EXPORT_SEND_FAILED, message="boom", priority=ac_service.PRODUCTION_EXPORT_SEND_FAILED_PRIORITY)`:
  `test_production_export_send_failed_true_while_active`, `..._true_when_dismissed` (set one row to `DISMISSED`, assert still True), `..._false_when_resolved` (`ac_service.resolve_for_role`), `..._false_without_ac`. Assert on `ac_service.production_export_send_failed(article)` and on `conditions.production_export_send_failed(article.articleworkflow, user)`.
- [ ] **Step 2: Run** `pytest wjs/plugins/wjs_review/tests/test_resend_production_zip.py` and see it fail (missing names).
- [ ] **Step 3: Implement.** `for_code` filters by `code` in the queryset; `ac_service.production_export_send_failed` returns `AttentionCondition.objects.displayable().for_code(PRODUCTION_EXPORT_SEND_FAILED).for_articles([article.pk]).exists()`; the condition delegates to it with `workflow.article`.
- [ ] **Step 4: Run the tests**, expect pass.
- [ ] **Step 5: Commit** (per the commit strategy chosen for the flow): `feat(wjs_review): add production export send failed condition`.

### Task 2: Resend logic

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py` (`SendProductionXMLToPublisher`, new `ResendProductionXMLToPublisher`)
- Test: `wjs/plugins/wjs_review/tests/test_resend_production_zip.py`

**Interfaces:**
- Consumes: `ac_service.production_export_send_failed` (Task 1).
- Produces: `SendProductionXMLToPublisher.send_error: Optional[str]` (`dataclasses.field(default=None, init=False)`), set to `str(exc)` when a non-silent send fails and swallowed, left `None` otherwise; `ResendProductionXMLToPublisher(articleworkflow: ArticleWorkflow)` with `run() -> ArticleWorkflow`.

- [ ] **Step 1: Write failing tests** (patterns: copy the settings override, `mock.patch("plugins.wjs_review.metadata_export.publishers.send_zip_to_iop", ...)` and the `communication_utils.log_operation` patch from `tests/test_production.py` around line 2540). Each starts from an active attention condition made as in Task 1:
  - `test_resend_success_logs_message_and_resolves_ac`: send mock called once, `log_operation` called, every attention condition row for the code is `RESOLVED`.
  - `test_resend_failure_keeps_ac_with_new_message`: send raises `SFTPSendError("still refused")`; `pytest.raises(ValidationError)`; the row is still `ACTIVE` and its message contains "still refused" (Review Focus 1).
  - `test_resend_with_dismissed_ac_sends`: row `DISMISSED`, send mock called once (Review Focus 2).
  - `test_resend_without_active_ac_raises_and_does_not_send`: no attention condition, `ValidationError`, send mock not called (also covers a second click after success, Review Focus 3).
  - `test_resend_without_send_function_raises`: `WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS = {}`, leftover attention condition, `ValidationError`, send mock not called (Review Focus 4).
  - `test_sender_records_send_error_only_on_swallowed_failure`: `send_error` is `None` on success and the error text after a swallowed failure.
- [ ] **Step 2: Run them**, expect fail.
- [ ] **Step 3: Implement.** Add `send_error` to the sender and set it in its failure branch. `ResendProductionXMLToPublisher.run()`: inside `transaction.atomic()` check the guards (send function configured for the journal via the same settings lookup as the sender, and the attention condition applies; else `ValidationError`), then run `SendProductionXMLToPublisher` non-silently and keep its `send_error`; **after the `with` block** raise `ValidationError(send_error)` if set, so the sender's attention-condition refresh commits. Return the workflow on success.
- [ ] **Step 4: Run the new tests and the existing sender tests** (`-k "send_production_xml_to_publisher or export_production_zip"`), expect all pass.
- [ ] **Step 5: Commit:** `feat(wjs_review): add logic to resend the production zip to the publisher`.

### Task 3: Action, view, URL and docs

**Files:**
- Modify: `wjs/plugins/wjs_review/states.py` (new `RESEND_PRODUCTION_ZIP_ACTION` after `PUBLISHER_CONFIRMED_ACTION`, added to `ReadyForTypesetter`, `TypesetterSelected`, `Proofreading`, `ReadyForPublication` where `PUBLISHER_CONFIRMED_ACTION` appears; confirm no other production state can still hold the attention condition), `wjs/plugins/wjs_review/views__production.py` (`ResendProductionXMLToPublisherView`), `wjs/plugins/wjs_review/urls.py`, `wjs/plugins/wjs_review/CLAUDE.md`
- Test: `wjs/plugins/wjs_review/tests/test_resend_production_zip.py`

**Interfaces:**
- Consumes: `ResendProductionXMLToPublisher` (Task 2), `conditions.production_export_send_failed` (Task 1).
- Produces: URL name `wjs-resend-production-zip`, path `article/<int:pk>/resend-production-zip/`, POST only; `RESEND_PRODUCTION_ZIP_ACTION` (permission `permissions.has_eo_role_by_article`, `is_post=True`, label "Resend to publisher", confirmation text).

- [ ] **Step 1: Write failing tests**, mirroring `tests/test_publisher_confirmation.py` for the client and EO login:
  - `test_action_available_to_eo_only_while_ac_applies`: via `BaseState.get_state_class(workflow).article_actions`, `.is_available(workflow, eo)` is True with the attention condition and False after `resolve_for_role`; False for a typesetter with the condition.
  - `test_view_eo_post_resends_and_redirects`: success flash, redirect to `wjs_article_details`, send mock called once.
  - `test_view_failed_resend_flashes_error`: error flash with the SFTP message, attention condition still active.
  - `test_view_refuses_non_eo_and_does_not_send`: typesetter and anonymous get refused, send mock not called (Review Focus 5); GET not allowed.
- [ ] **Step 2: Run them**, expect fail.
- [ ] **Step 3: Implement.** The view copies `SetPublisherConfirmedFlagView` (`AuthenticatedUserPassesTest`, `View`, `http_method_names = ["post"]`, EO via `base_permissions.has_eo_role`), runs `ResendProductionXMLToPublisher(...).run()`, catches `(ValueError, ValidationError)` into `messages.error`, success into `messages.success`, always redirects to `wjs_article_details`. Register the path in `urls.py` beside `wjs_set_publisher_confirmed`. Document the new write path in the attention-conditions section of `wjs_review/CLAUDE.md`.
- [ ] **Step 4: Run** the new test file plus `test_actions.py`, `test_publisher_confirmation.py` and `test_production.py -k "send_production_xml"`; expect pass. Run `pre-commit run --files <changed files>`.
- [ ] **Step 5: Commit:** `feat(wjs_review): let the EO resend the production zip to the publisher`.

---

## After the independent review

The review changed the implementation in three places (stage forcing, stale-row resolution, `PublicationInProgress`
action). They are described in "Changes after the independent review" of the spec; this plan is kept as written.

## Self-review notes

- Spec coverage: logic, outcome reporting, condition, action, view/URL, docs and all test groups map to Tasks 1-3; non-goals need no task.
- One deviation from the spec text, flagged for review: "active" is implemented as ACTIVE or DISMISSED ("the condition still applies"), because a dismissal only sets the row aside.
- Outcome reporting uses a `send_error` attribute on the sender (smallest change; existing callers and their `run()` return value are untouched).
