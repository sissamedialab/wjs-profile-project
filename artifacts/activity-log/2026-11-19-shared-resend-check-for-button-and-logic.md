## 2026-11-19 — Shared resend check for the production zip button and logic (MR !1643 review)
**What:** The "Resend to publisher" button and `ResendProductionXMLToPublisher` now use one check, `conditions.production_zip_resend_blocker`. A resend needs a production state, a journal with a send function, and the send failure still applying. The resend also locks the workflow row.
**Why:** In the review on `conditions.py`, the button showed whenever the failure applied, but the logic also required a send function, and neither side checked the state. So the button could appear for a resend the logic refuses, for example after `rollback_acceptance`.
**Decisions:**
- The blocker returns the reason, or `None`. The logic raises that reason as `ValidationError`, so button and logic cannot drift apart. `conditions.production_export_send_failed` is replaced by `can_resend_production_zip`.
- The allowed states live in `RESEND_PRODUCTION_ZIP_STATES`, and a test keeps them in sync with the states that declare the action.
- Two simultaneous resends: the reviewer accepted an error for the second one. A row lock serialises them, so the second sees the failure resolved and is refused instead of sending again.
- This settles three deferred minors from `2026-11-19-eo-resend-of-failed-production-zip.md`: the double-POST lock, the state guard after `rollback_acceptance`, and the duplicated settings lookup.
- E2e: skipped by the decision pass, as the change only alters server-side button visibility and adds no client interaction.

**Considered & dropped:** checking only the attention condition in the logic: it would allow resends on journals with no send function.
- Wrap-up follow-up commit:
  - `test_simultaneous_resends_send_once`: two threads, so two DB connections. It fails with the lock removed, because the second resend does not wait and both send.
  - `run()` reuses the locked row's state.
  - The `CLAUDE.md` paragraph is rewrapped.

**Follow-ups:**
- The plan still names the removed `conditions.production_export_send_failed`.
- Local env: wjs-submission on `wjs-develop` needs wjs-themes ≥ the `ArticleSearchFilterMixin` release, but the venv still has an older non-editable wjs-themes. The test DB also needs `--create-db` for the specs#3295 `cas_text` column.

**Refs:** specs#3450, MR !1643, commit `b0480a85`. Eval: 77% — artifacts/evaluations/2026-11-19-resend-production-zip-2.md
