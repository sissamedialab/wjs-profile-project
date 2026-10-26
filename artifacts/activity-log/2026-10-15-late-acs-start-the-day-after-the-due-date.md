## 2026-10-15 — Typesetter/proofing late ACs start the day after the due date
**What:** The `typesetter_late` and `author_proofing_late` attention conditions now fire only from the day after the due date (never "0 days late"), compare local dates, and say "1 day" / "N days". `RequestProofs` now computes the proofing due date from the local date.
**Why:** wjs/specs#3264: the AC appeared on the due date as "Typesetter is 0 days late". Stakeholder rule (note 78773): "if the previous action took place on 10.10, the AC should start on 14.10". The typesetting `due` is always last action + `TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` (3) and is never edited afterwards (verified: three creation sites, no form, imports only delete), so the rule is exactly `today > due`; the setting stays at 3. The stakeholder later confirmed the same rule for author proofing.
**Decisions:**
- `timezone.localdate()` instead of `timezone.now().date()`: `due` is stored as a local date (typesetting `DateField`) or local midnight (proofing `DateTimeField`); the UTC date was off by one between local midnight and 01:00/02:00.
- Proofing: besides "late by 0 days", the "Was expected by" date printed the UTC value, i.e. the day *before* the due date — fixed by formatting the local due date.
- Messages stay untranslated plain f-strings like the rest of `conditions.py`; a small `_days` helper is shared by both conditions.
- `RequestProofs._create_proofing_assignment` uses the local date (2-line change, added after review on user request). Existing assignments are not migrated; stale "0 days" AC rows are resolved by the evaluator on the next rebuild.
- Tests run with `-p no:freezegun`: `pytest-freezegun` in the py3.13 `janeway` venv imports the removed `distutils`. The new tests use `freezegun` directly.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming | ≈40k | ≈15m |
| Design | superpowers:writing-plans | ≈30k | ≈10m |
| Implementation | superpowers:executing-plans (inline, 3 tasks) | ≈60k | ≈30m |
| Review | superpowers:requesting-code-review (opus reviewer, task 1) | ≈70k | ≈2m |
| Review | superpowers:requesting-code-review (opus reviewer, task 2 delta) | ≈84k | ≈1.5m |
| Review | nephila-core-conventions:code-eval | ≈10k | ≈3m |

**Considered & dropped:** Changing `TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS` to 4 (would move the due date shown to the typesetter, not just the AC); `ngettext` for pluralization (no other message in `conditions.py` is translated).
**Follow-ups:**
- `_evaluate_typesetter_late` / `_evaluate_author_proofing_late` loop over all assignments of the article, including completed or earlier-round ones (pre-existing; candidate issue).
- Deferred test minors: typesetter stale-row test docstring says "0 days late" but creates a "1 day late" row; optional `isinstance` guard for an in-memory `date` `due` in `is_author_proofing_late`.
- Local env: fix `pytest-freezegun` on py3.13 (also used via the `freezer` fixture in `test_newsletters.py`, `test_metadata_export.py`); 6 `test_production.py` galley/publication tests fail locally on baseline too.

**Refs:** wjs/specs#3264 (note 78773); spec `artifacts/specs/2026-10-15-typesetter-late-ac-threshold-design.md`; plan `artifacts/plans/2026-10-15-typesetter-late-ac-threshold.md`.
Eval: 83% — artifacts/evaluations/2026-10-15-typesetter-late-ac-threshold.md
