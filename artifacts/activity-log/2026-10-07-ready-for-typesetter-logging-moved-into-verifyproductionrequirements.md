## 2026-10-07 — Ready-for-typesetter logging moved into VerifyProductionRequirements
**What:** Following the review of MR !1551 (i.spalletti), the module-level helper `log_ready_for_typesetter(article, actor)` became the public method `VerifyProductionRequirements.log_ready_for_typesetter(actor=None)`. `ConfirmProductionReadiness` now calls it through `VerifyProductionRequirements(self.workflow)`. Behaviour is unchanged.
**Why:** It keeps the logging inside the logic class that owns the transition, in line with the per-class `_log_*` methods used across `logic__production.py`. This corrects the "one helper" decision in `2026-10-06-timeline-message-for-ready-for-typesetter.md`.
**Decisions:** The method is public, not `_log_operation`, because a second class (`ConfirmProductionReadiness`) calls it.
**Refs:** wjs/specs#3178; MR !1551, note 77836.
