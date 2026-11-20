## 2026-11-18 — StaffKeyword unique per (parameters, keyword)
**What:** Added a `UniqueConstraint` on `StaffKeyword(parameters, keyword)` (migration `jcom_profile/0046`) and made both wjapp importers merge duplicate source keywords, keeping the highest weight.
**Why:** The JCAP import produced editors linked to the same keyword twice. Duplicates double-counted the keyword in the editor `keyword_score` (ranking), and made `StaffWorkloadParametersForm.save()` crash on `get_or_create`.
**Decisions:**
- Highest weight wins on merge (the director sets weights deliberately; keeping the lowest `pk` could drop the strongest signal).
- Constraint-only migration, no dedupe step: current data is clean, and the migration must fail loudly if a database still has duplicates. No management command (dead code).
- `UniqueConstraint` instead of legacy `unique_together`; added `Meta` with `verbose_name`, `ordering = ["parameters", "keyword"]` per the repo model rules.
- `sync_editor` never overwrites an existing association's weight (an EO may have corrected it), so it uses `get_or_create`.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming | not measured | not measured |
| Design | superpowers:writing-plans | not measured | not measured |
| Implementation | superpowers:executing-plans (inline) | not measured | not measured |
| Review | opus whole-branch reviewer | ≈110k | ≈1m |
| Review | nephila code-eval | not measured | not measured |

**Considered & dropped:** Dedupe inside the migration (deletes rows during a deploy for a case that does not exist); a separate dedupe command; code guards only without a constraint (other write paths could still create duplicates).
**Follow-ups:** Deferred minors from the review: formset row order on the director editor-parameters page changed (order by `keyword__word` if wanted); no DB test for `set_editor_parameters`; merge warning is logged under `import_logic`'s logger. Test DBs are reused: after a constraint or schema change run pytest with `--create-db`.
**Eval:** 80% — artifacts/evaluations/2026-11-18-staffkeyword-unique.md
**Refs:** wjs/specs#3436, wjs/specs#2316 (JCAP import).
