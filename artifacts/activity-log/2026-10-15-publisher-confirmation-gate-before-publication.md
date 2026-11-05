## 2026-10-15 — Publisher green light gate before publication (JCAP)
**What:** EO can record (and revoke) "the publisher confirmed the paper is publishable" in RFT, TYP_SELECTED, PROOFREADING and RFP; for journals in `WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION` (default `["JCAP"]`) `BeginPublication` refuses to start without it and the "Publish" button is greyed out with a hover explanation.
**Why:** IOP must clear each JCAP paper before the EO publishes it; nothing in the workflow recorded that green light.
**Decisions:**
- Scoped by a setting (JCAP only), not global: blocking JCOM/JCOMAL publication was never intended.
- Predicates on the model (`requires_publisher_confirmation()` / `publisher_confirmation_missing()`), not in `conditions.py`: `logic__production` can't import `conditions` (which imports `logic`) without a cycle.
- Flag does not take part in `can_be_set_rfp()`; existing/imported JCAP papers start at `False` and need an explicit EO confirmation. Importers untouched.
- Explicit set (`?confirmed=1|0`, `SetPublisherConfirmedFlag`) instead of a toggle, after final review: with a toggle a stale "Revoke" click re-confirms the paper and opens the gate. A request for the value already stored is refused with an error. Save uses `update_fields` so concurrent galley-test writes on the same row survive.
- Label stays "Publish" (user decision). Disabled actions can declare `disabled_cause`; the template wraps them in a hoverable `<span title=…>` because Bootstrap `.disabled` (`pointer-events: none`) hides the button's own `title`. Same pattern as `reviewers_list.html`; wjs-themes has no override of these templates.
- Tests in a dedicated `tests/test_publisher_confirmation.py` to use pytest-django's `settings` fixture (test journal is JCOM).
- Gotchas: janeway venv currently lacks `setuptools` (no `distutils`), so `pytest-freezegun` fails to load on Python 3.13 — tests run with `-p no:freezegun`. The new column needs `--create-db` once on a reused test DB. Six `test_production.py` galley/publication tests still fail on clean `wjs-develop` (they stall after the gate, in galley generation).
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming (inline) | ≈40k | ≈15m |
| Design | superpowers:writing-plans (inline) | ≈45k | ≈10m |
| Implementation | superpowers:executing-plans (inline, incl. post-review fixes) | ≈130k | ≈90m (mostly test runs) |
| Review | code reviewer subagent (opus, general-purpose) | ≈88k | ≈2m |
| Review | nephila-core-conventions:code-eval (inline) | ≈15k | ≈3m |

**Considered & dropped:** plain toggle mirroring "Mark as Unpublishable" (stale-page re-confirmation risk); changing the Publish label to explain the wait (user preferred keeping "Publish"); applying the gate to all journals.
**Follow-ups:** deferred review minors — no success message after set/revoke; forged-POST test could also assert `date_published`/pubid unchanged; `test_default_setting_lists_only_jcap` restates a constant; no view-level refusal test for PUBLICATION_IN_PROGRESS/PUBLISHED. Reinstall `setuptools` in the janeway venv.
**Refs:** wjs/specs#2914 (related #2678); spec `artifacts/specs/2026-10-13-publisher-confirmation-gate-design.md`; plan `artifacts/plans/2026-10-13-publisher-confirmation-gate.md`.
Eval: 83% — artifacts/evaluations/2026-10-15-publisher-confirmation-gate.md
