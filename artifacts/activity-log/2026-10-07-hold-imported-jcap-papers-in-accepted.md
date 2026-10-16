## 2026-10-07 — Hold imported JCAP papers in Accepted, EO sets the affiliation (#3131)
**What:** JCAP papers imported from wjapp (populated `preprintid`) are held in Accepted like TA papers until the EO releases them; the wjapp import is never blocked by the hold; the EO can set the paper affiliation from the `ArticleSubmission` advanced admin (wjs-submission-project). The check is renamed `jcap_production_readiness_not_confirmed`.
**Why:** wjapp has no affiliations, and the production zip sent to IOP needs one, so the EO must add it before production. Requirements clarified with the contact person in several rounds (JCAP only; same notification and AC as TA; import must complete when the paper already went past acceptance in wjapp).
**Decisions:**
- One OR condition (TA or imported) in the existing check, not a second check function: one message for imported TA papers.
- The importer replays wjapp through the real WJS logic, so the replayed acceptance always holds an imported paper. Two helpers in `import_logic.py`: `release_production_readiness_hold` (called in `TYP_TAKES_CHARGE`, i.e. when the history goes on) and `finalize_production_readiness_hold` (end of import: held paper → JCAP message unread again; any other state → AC resolved).
- The import release follows today's automatic path exactly (FSM transition, so the zip is sent to IOP; "ready for typesetter" timeline message), dated at the wjapp action via freezegun. Explicit developer decision after weighing "no IOP send during import".
- Admin affiliation choices = correspondence author's affiliations + the stored one (a stale stored value must not block saving other fields). No help text.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + clarification rounds (inline) | ~250k | ~3h over several days |
| Design | superpowers:writing-plans (inline) | ~40k | ~20m |
| Implementation | superpowers:executing-plans (inline, TDD) | ~60k | ~40m |
| Review | code-reviewer subagent, opus (dropped first implementation) | ~107k | ~4m |
| Review | code-reviewer subagent, opus (final) | ~139k | ~6m |
| Review | code-eval + doc-sync (inline) | ~15k | ~10m |

Eval: 78% — artifacts/evaluations/2026-10-07-jcap-imported-papers-affiliation.md
**Considered & dropped:**
- First implementation (2026-10-05, TA-or-imported check only + admin): dropped before merge because the import replay failed for papers past acceptance in wjapp (`AssignTypesetter` needs Ready for Typesetter).
- Stopping the replay at Accepted, skipping later wjapp actions: replaced by "release when the history goes on" after clarification.
- Import release setting the state directly to avoid re-sending to IOP: dropped by the developer in favour of behaving exactly like today.
**Follow-ups:**
- Deferred review minors: finalize call outside the importer's try/except; read hold messages stay in the history of papers released during import (mention in MR); admin save test has no access mode; no test of the importer wiring.
- IOP metadata mapper uses `submission_data.affiliation` only for TA papers: separate task.
- Required action at deploy: any `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` override must use the new dotted path.
- Gotchas: tests need `PYTHONPATH=<wjs-profile-project>` because the venv's `wjs` is a non-editable namespace copy; reused `-n7` test DBs go stale on new columns (use `--create-db`, serial if Postgres runs out of shared memory). Known local failures: 6 in `test_production.py` + `test_withdraw_preprint_press_notification[rfp_article-5]`.
**Refs:** wjs/specs#3131 (extends #2591, #2472; related #2912, #3174, #3178); spec `artifacts/specs/2026-10-07-jcap-imported-papers-affiliation-design.md`, plan `artifacts/plans/2026-10-07-jcap-imported-papers-affiliation.md`.
