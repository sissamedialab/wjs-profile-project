# JCAP imported papers: hold in Accepted and let the EO set the affiliation

- **Issue:** wjs/specs#3131 (extends wjs/specs#2591, itself built on wjs/specs#2472)
- **Projects:** wjs-profile-project (`wjs/plugins/wjs_review`, `wjs/jcom_profile`), wjs-submission-project
  (`wjs/plugins/wjs_submission`)
- **Base:** `wjs-develop` at `cee36c74` (wjs-profile-project) and `efbcbd8` (wjs-submission-project)
- **Date:** 2026-10-07
- **Status:** design agreed in conversation, pending written-spec review. Supersedes the 2026-10-05 draft,
  which was dropped before merge.

## Context

Papers imported from wjapp carry no affiliation: wjapp only stored the author's country, and the importer
only sets `Account.country`. `ArticleSubmission.affiliation`, the paper-level affiliation also used for the
TA check at submission, stays empty. For JCAP the EO must set it by hand before the paper goes into
production, because the production zip sent to IOP needs it.

What exists today on `wjs-develop`:

- `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]` runs
  `wjs_review.events.checks_after_acceptance.jcap_ta_not_yet_confirmed` when a paper is accepted
  (`VerifyProductionRequirements`). It returns `False` only for the TA access mode (#2591): the paper is
  held in Accepted, the EO gets the "Issues after acceptance" message and the JCAP message
  (`jcap_ta_pending_subject` / `jcap_ta_pending_body`), and the EO attention condition
  `ACCESS_MODE_TO_CHECK` ("Access mode to check", #3174) is created.
- The EO releases the paper with "Confirm production readiness" (`ConfirmProductionReadiness`): the FSM
  transition `system_verifies_production_requirements` moves it to Ready for Typesetter, the "ready for
  typesetter" timeline message is logged (#3178) and `ACCESS_MODE_TO_CHECK` is resolved.
- The transition `system_verifies_production_requirements` itself calls `SendProductionXMLToPublisher`
  (#2912, #2972): for JCAP it builds the production zip and sends it over SFTP to IOP.
- Only the wjapp importers create the `preprintid` identifier, so a populated `preprintid` marks an
  imported paper.
- The importer (`import_articles_from_wjapp`) creates the `preprintid` before replaying the wjapp history;
  the replay goes through the real WJS logic (`ED_ACC_DOC` → `HandleDecision` → `VerifyProductionRequirements`;
  `TYP_TAKES_CHARGE` → `AssignTypesetter`, which needs Ready for Typesetter). After the replay it marks
  every message of the paper as read (`mark_all_messages_read`) and, for published papers, forces the
  state to PUBLISHED.
- `ArticleSubmissionAdmin` on the advanced admin site lets the EO edit access mode, licence and copyright,
  not the affiliation.

## Requirements (confirmed with the contact person)

1. JCAP only.
2. A paper is **imported** when it has a populated `preprintid` identifier.
3. An imported paper never moves automatically from Accepted to Ready for Typesetter: the EO must release
   it, exactly as for TA papers. One condition with an OR (TA or imported) in the same check function,
   whether or not an affiliation is already set.
4. Held imported papers get the same EO notification and the same `ACCESS_MODE_TO_CHECK` attention
   condition as TA papers. No copy changes.
5. The EO releases the paper with the existing "Confirm production readiness" action, which does not check
   the affiliation.
6. The EO can set the paper affiliation (`ArticleSubmission.affiliation`) from the advanced admin.
7. **The import is never blocked.** The whole wjapp history is replayed:
   - a paper whose wjapp history stops at acceptance stays held in Accepted, with `ACCESS_MODE_TO_CHECK`
     active and the JCAP message **unread**;
   - a paper whose wjapp history goes on (production, publication) reaches its real state, and nothing of
     the hold is left behind (no active `ACCESS_MODE_TO_CHECK`).
8. Rename `jcap_ta_not_yet_confirmed` to `jcap_production_readiness_not_confirmed`.

## Decisions taken in this design (confirmed by the developer)

- **The import release behaves exactly like today's automatic path.** It runs the FSM transition
  `system_verifies_production_requirements`, so the production zip is sent to IOP as it is today on
  `wjs-develop` for every JCAP paper accepted in wjapp, and it logs the "ready for typesetter" timeline
  message with the system as actor, as `VerifyProductionRequirements` does when a paper passes the checks.
- Consequence: a paper whose wjapp history goes past acceptance is sent to IOP during its import (as today);
  a paper held at acceptance is sent when the EO releases it.

## Design

### 1. Acceptance check (wjs-profile-project, `wjs/plugins/wjs_review/events/checks_after_acceptance.py`)

- Rename `jcap_ta_not_yet_confirmed` → `jcap_production_readiness_not_confirmed`, and update the dotted
  path in `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]` (`wjs/defaults/settings.py`). These are the
  only two references.
- Private helpers:
  - `_is_ta(article) -> bool`: the access mode has code `OA_CODE_TA`. A missing `submission_data` or a
    `None` access mode means "not TA" (no early `return True` any more, otherwise an imported paper without
    `submission_data` would get through).
  - `_is_imported(article) -> bool`: `bool(article.get_identifier("preprintid"))`. An empty value is not
    populated.
- If neither: return `True`. Otherwise send the existing JCAP message to the EO and return `False`.
- The subject rendering is extracted into a public helper
  `render_production_readiness_subject(article) -> str`, used by the check and by the importer (section 2)
  to find the JCAP message.
- The settings `jcap_ta_pending_subject` / `jcap_ta_pending_body` keep their names and values (no copy
  changes; renaming settings would need a data migration).

### 2. Import (wjs-profile-project, `wjs/jcom_profile`)

The importer has no test harness (it needs a live wjapp database), so the behaviour lives in two helpers in
`import_logic.py`, unit-tested on their own, and the importer only calls them.

- `release_production_readiness_hold(article) -> bool`: if the article's workflow is in ACCEPTED, run the
  FSM transition `system_verifies_production_requirements` and save (the zip is sent to IOP, as on the
  automatic path), log the "ready for typesetter" timeline message with the system as actor
  (`VerifyProductionRequirements(workflow).log_ready_for_typesetter()`), resolve `ACCESS_MODE_TO_CHECK` for
  every user (`ac_service.resolve_all_for_article`), log a warning and return `True`; otherwise do nothing
  and return `False`.
- `finalize_production_readiness_hold(article) -> None`, run once at the end of the import, after the final
  state is settled:
  - workflow in ACCEPTED (history stopped at acceptance, paper held): flag the JCAP message(s) as unread
    again for the EO (`MessageRecipients.read=False` for the recipients, `Message.read_by_eo=False`),
    matching the subject from `render_production_readiness_subject(article)`;
  - any other state: resolve `ACCESS_MODE_TO_CHECK` for every user (covers papers forced to PUBLISHED or
    REJECTED with an incomplete history, which never pass through the release step).
- Wiring in the importer:
  - `TYP_TAKES_CHARGE.run()` calls `release_production_readiness_hold(self.article)` before
    `AssignTypesetter`. It is the wjapp step that moves a paper out of Ready for Typesetter, so this puts the
    paper exactly where it was before #3131 at that point of the replay; later production steps are
    unchanged.
  - `import_articles_from_wjapp` calls `finalize_production_readiness_hold(article)` after the final state
    handling (after the PUBLISHED / REJECTED forcing, before the reminders cleanup).
- Non-JCAP papers are never held (the check is configured for JCAP only), so both helpers are no-ops for
  them in practice: the release only acts on ACCEPTED, and a JCOM/JCOMAL paper is never left in ACCEPTED by
  the replay; the finalize step finds no JCAP message and no `ACCESS_MODE_TO_CHECK`.

### 3. Affiliation in the advanced admin (wjs-submission-project, `wjs/plugins/wjs_submission/advanced_admin/`)

- `ArticleSubmissionAdmin.fields`: add `"affiliation"` right after `"access_mode"`.
- `ArticleSubmissionAdminForm`: add `"affiliation"` to `Meta.fields`; in `__init__`, for a saved instance,
  restrict the queryset to the correspondence author's affiliations (as in submission step 4) **plus the
  stored affiliation**, so that an affiliation left from a previous correspondence author does not make every
  save fail; no correspondence author → only the stored one, or empty. The field stays optional.
- Saving writes `submission_data.affiliation` and does not recompute the access mode; licence and copyright
  behave as today.
- No help text: where the EO finds the affiliation is outside this feature.

## Testing

wjs-profile-project:

- `wjs/plugins/wjs_review/tests/test_checks_after_acceptance.py` (new; the #2591 behaviour has no unit tests):
  TA held + one message to the EO; imported held + message; imported TA → one message; imported without
  `ArticleSubmission` held; native non-TA passes, no message; no submission data and no preprintid passes;
  empty preprintid passes; only JCAP is configured with the check (by its new dotted path); end to end,
  accepting an imported paper leaves it in Accepted with `ACCESS_MODE_TO_CHECK` active.
- `wjs/plugins/wjs_review/tests/test_import_production_readiness_hold.py` (new; it lives next to the
  `wjs_review` fixtures that build a paper held by a real acceptance):
  - release: moves ACCEPTED → READY_FOR_TYPESETTER, resolves the AC, calls `SendProductionXMLToPublisher`
    once and logs the "ready for typesetter" timeline message; after it, `AssignTypesetter` (as
    `TYP_TAKES_CHARGE` does) succeeds; on a paper not in ACCEPTED it does nothing (no send, no message);
  - finalize: held paper → JCAP message unread again after `mark_all_messages_read`, AC still active;
    paper in another state (e.g. forced PUBLISHED) → AC resolved; other messages untouched.

wjs-submission-project, `tests/test_admin_affiliation.py` (new): affiliation is an admin field; choices are
the correspondence author's affiliations only; another account's affiliation is rejected; saving stores it
and leaves access mode, licence and copyright unchanged; a stored affiliation outside the author's ones stays
selectable and valid; no correspondence author → empty choices, form valid.

## Out of scope

- IOP metadata mapper using `submission_data.affiliation` for non-TA papers: separate task.
- Creating affiliations from the admin; recomputing the access mode from the affiliation.
- Renaming the `jcap_ta_pending_*` settings.

## Delivery

- Branch `feature/issue-3131-imported-papers-affiliation` in both repositories, from `wjs-develop`; two MRs
  that can merge in any order.
- No migrations, no new dependencies.
- **Required action (wjs-profile-project MR):** any deployment that overrides
  `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` in its own settings must switch to the new dotted path
  `plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed`, otherwise
  accepting a JCAP paper fails (`import_string` cannot find the old name).
