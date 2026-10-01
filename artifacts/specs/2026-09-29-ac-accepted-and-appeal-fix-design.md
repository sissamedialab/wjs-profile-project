# AC fixes on paper acceptance: stale "Appeal to submit" and new "Access mode to check"

- **Issue:** wjs/specs#3174 (from the JCAP go-live feedback wjs/specs#3162)
- **Project:** wjs-profile-project, `wjs/plugins/wjs_review`
- **Date:** 2026-09-29
- **Status:** approved design

## Problem

When a JCAP paper enters the `Accepted` state, the papers list shows the
attention condition (AC) "Appeal to submit". The expected AC is "Access mode to
check".

Analysis showed two independent defects.

1. **Regression of the materialized ACs.** `APPEAL_TO_SUBMIT` (author) is
   created when an appeal is opened (`OpenAppeal.run`, which evaluates the
   `UnderAppeal` ACs), but nothing resolves it when the author submits the
   appeal. `AuthorHandleRevision.run` resolves only the revision/metadata
   lateness ACs. The code is event-based, so neither
   `_resolve_stale_time_based_acs` nor the nightly
   `rebuild_attention_conditions` ever heals it. The same holds for
   `APPEAL_LATE` (EO) on the event-driven path. With the old, non-materialized
   implementation the message was computed live by
   `UnderAppeal.article_requires_author_attention`, so it could not leak into
   later states.
2. **Missing AC.** Papers can be held in `Accepted` by the
   `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` checks (today:
   `jcap_ta_not_yet_confirmed`, for JCAP papers with the Transformative
   Agreement access mode) until the EO clicks "Confirm production readiness".
   No AC exists for `Accepted`, neither in the old implementation nor in the
   materialized one; the need was flagged in wjs/specs#2471 ("verify the need
   for an attention condition") but never specified.

## Requirements

- R1. "Appeal to submit" is resolved as soon as the paper is no longer
  `UnderAppeal` because the author submitted the appeal. `APPEAL_LATE` (EO) is
  resolved at the same point.
- R2. Stale `APPEAL_TO_SUBMIT` / `APPEAL_LATE` rows already in the database on
  papers that are no longer `UnderAppeal` are resolved by a data migration.
- R3. A new AC "Access mode to check" is set, for the **EO role only**, for
  **every paper held in `Accepted`** (not only TA papers).
- R4. "Access mode to check" is resolved when the paper leaves `Accepted`
  through the EO's "Confirm production readiness" action. Withdrawal already
  resolves all ACs.
- R5. No Django signals: every AC change is an explicit `ac_service` call in a
  logic class, in the `# -- Materialized AC updates --` block, as in the rest
  of the codebase.

## Design

### 1. Clear the appeal ACs on appeal submission (R1)

In `AuthorHandleRevision.run` (`logic.py`), inside the existing
`# -- Materialized AC updates --` block, add explicit resolves guarded by the
existing `_was_under_appeal()` helper:

```python
if self._was_under_appeal():
    # Appeal submitted: the UnderAppeal ACs no longer apply
    ac_service.resolve_for_role(article, "author", ac_service.APPEAL_TO_SUBMIT)
    ac_service.resolve_for_role(article, "eo", ac_service.APPEAL_LATE)
```

At that point `_trigger_complete_event` has already run the
`restart_review_process_after_revision_submission` handler, which calls
`author_submits_appeal()` and moves the paper to `EditorSelected`.

Explicit codes are preferred over "resolve everything mapped to the previous
state": it matches the neighbouring lines and the other state-exit sites
(`ReadyForPublication.run`, `HandleEOSendBackToTypesetter.run`,
`RequestProofs.run`, `AuthorSendsCorrections.run`).

`AuthorHandleRevisionObsolete` is not touched.

### 2. Data migration for the stale appeal ACs (R2)

New migration `wjs_review/migrations/0018_resolve_stale_appeal_acs.py`
(depends on `0017_blacklisted_authoremail`). It runs a `RunPython` forward
function that bulk-updates to `resolved` all `AttentionCondition` rows with:

- `code` in `{"appeal_to_submit", "appeal_late"}`,
- `status = "active"`,
- `article__articleworkflow__state != "UnderAppeal"`.

Codes and states are hard-coded as literals: migrations must not import
runtime constants. Reverse is `migrations.RunPython.noop`.

### 3. New AC "Access mode to check" (R3)

In `ac_service.py`:

- New event-based code constant, next to the other event-based ones:
  `ACCESS_MODE_TO_CHECK = "access_mode_to_check"`, with a docstring
  ("EO: the accepted paper is held in Accepted; access mode to be checked
  before production").
- New map entry: `("Accepted", "eo"): [ACCESS_MODE_TO_CHECK]`.
- New evaluator `_evaluate_access_mode_to_check(self, roles)`. It always
  upserts the message "Access mode to check" for the given roles, like
  `_evaluate_appeal_to_submit`: the state map already restricts it to
  `Accepted`.

Adding `Accepted` to `STATE_ROLE_AC_MAP` also makes the nightly rebuild and
`populate_attention_conditions` iterate it. Papers already held in `Accepted`
(e.g. 4409) therefore get the AC within 24 hours of deploy, with no dedicated
migration.

**Creation point.** In `VerifyProductionRequirements.run`
(`logic__production.py`), in the branch where
`_check_conditions()` fails and the paper stays in `Accepted`, add:

```python
# -- Materialized AC updates --
# Paper held in Accepted: the EO must check it before production.
evaluator = ac_service.ACStateEvaluator(state=self.articleworkflow.state, article=self.articleworkflow.article)
evaluator._evaluate_code(ac_service.ACCESS_MODE_TO_CHECK)
```

This mirrors `ReadyForPublication.run`, which creates the event-based
`MISSING_*` ACs on entry. Papers that pass the checks move straight to
`ReadyForTypesetter` and never get the AC.

### 4. Resolve "Access mode to check" on EO confirmation (R4)

`EOConfirmsProductionReady.post` (`views__production.py`) currently calls the
FSM transition directly. Move the behaviour into a new logic class
`ConfirmProductionReadiness` in `logic__production.py` (a different name from
the view, to avoid confusion), that:

1. checks `has_transition_perm(workflow.system_verifies_production_requirements, user)`
   and raises `ValueError` if the transition is not allowed;
2. runs the transition and saves the workflow, inside `transaction.atomic()`;
3. in a `# -- Materialized AC updates --` block, calls
   `ac_service.resolve_for_role(article, "eo", ac_service.ACCESS_MODE_TO_CHECK)`.

The view keeps its current messages and redirects: it turns the
`ValueError` into the existing error message.

The automatic success path of the acceptance checks needs no resolve (the AC
is never created there). `WithdrawPreprint.run` already resolves all ACs.

## Error handling

- Resolving an AC that does not exist is a no-op in `ac_service`.
- The new logic class reports a disallowed transition with `ValueError`; the
  view keeps today's user-facing behaviour.
- Everything runs inside the existing `transaction.atomic()` blocks, so an
  error rolls back both the state change and the AC update.

## Testing (TDD)

In `wjs/plugins/wjs_review/tests/test_attentionconditions.py` (or the closest
existing AC test module), using the existing fixtures:

1. Open an appeal, submit it: `APPEAL_TO_SUBMIT` (author) and `APPEAL_LATE`
   (EO) are no longer active; `article_requires_attention` for the author no
   longer returns "Appeal to submit".
2. A JCAP TA paper accepted by the editor stays in `Accepted`: the EO sees
   "Access mode to check"; the author and the editor do not.
3. A paper that passes the acceptance checks goes to `ReadyForTypesetter`
   and has no `ACCESS_MODE_TO_CHECK` row.
4. The EO confirms production readiness: the paper moves to
   `ReadyForTypesetter` and the AC is resolved. The confirmation view still
   redirects with the success message; a disallowed transition still shows the
   error message.
5. `ACStateEvaluator("Accepted", article).evaluate_all()` creates the AC for
   the EO (covers the nightly rebuild for pre-existing papers).
6. The data migration's forward function resolves stale appeal rows on papers
   not `UnderAppeal`, and leaves active rows on papers still `UnderAppeal`.

## Out of scope

- The duplicate "Issues after acceptance" notification to the EO
  (wjs/specs#3176).
- A generic state-exit cleanup (e.g. a `post_transition` receiver): signals
  are avoided by project convention.
- `BeginPublication` / `FinishPublication` not resolving `MISSING_*`: a
  similar leak, noticed during analysis, which deserves its own issue.
