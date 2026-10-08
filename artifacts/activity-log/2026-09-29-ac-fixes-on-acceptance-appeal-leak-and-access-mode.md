## 2026-09-29 — AC fixes on acceptance: appeal leak and "Access mode to check"
**What:** Fixed "Appeal to submit" leaking past UnderAppeal (plus a data migration for rows that had already leaked), and added the EO-only AC "Access mode to check" for papers held in Accepted, created by `VerifyProductionRequirements` and resolved by the new `ConfirmProductionReadiness` logic class and by `rollback_acceptance`.
**Why:** specs#3174 (JCAP go-live feedback #3162). The leak is a regression of the materialized ACs: the old implementation computed "Appeal to submit" live from the state, while the materialized row was never resolved and is event-based, so neither the stale cleanup nor the nightly rebuild heals it. "Access mode to check" never existed; it was flagged as "specifications needed" in #2471 and never specified.
**Decisions:**
- No signals (team convention): explicit resolves by code in the logic classes, following the `MISSING_*` pattern (created on entering the state, resolved on leaving it).
- `resolve_all_for_article(codes=[...])` instead of `resolve_for_role`, so rows of former role holders (an author changed during the appeal, an EO removed from the group) are resolved too.
- The AC is shown for every paper held in Accepted, not only TA papers (Andrea's call).
- `rollback_acceptance` also got the AC resolve, plus a fix for a pre-existing `TypeError` (`log_operation(article, …)`): the command could not run at all.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + history analysis (inline) | ~150k | ~40m |
| Design | superpowers:writing-plans (inline) | ~60k | ~15m |
| Implementation | superpowers:executing-plans (inline, TDD) | ~120k | ~45m |
| Review | code-reviewer subagent (opus) | ~145k | ~6m |
| Review | code-eval + doc-sync (inline) | ~15k | ~5m |

**Considered & dropped:** a generic `post_transition` receiver clearing the previous state's ACs (signals are avoided; it also fires on intermediate transitions); showing the AC only for TA papers; touching `AuthorHandleRevisionObsolete` (unreachable with the current `WJS_USE_WJS_SUBMISSION` settings).
**Follow-ups:**
- Central sweep of state-scoped event-based ACs on state exit: every new exit path can leak.
- `BeginPublication`/`FinishPublication` don't resolve `MISSING_*`.
- For QA: right after acceptance the EO first sees "You have unread messages" (priority 1); #3176 removes one of the two notifications.
- Deferred review minors: resolve in the success branch of `VerifyProductionRequirements`, a dedicated exception class for the view, `reverse()` in the view test, and a migration test for an article without a workflow.
- Gotcha: tests need the `janeway` venv to have this repo installed editable (`pip install -e . --no-deps`, plus `drf-spectacular[sidecar]`), and `-p no:freezegun` on Python 3.13. Seven tests in `test_production.py` and `test_logic.py` fail on a clean `wjs-develop` too.
**Refs:** wjs/specs#3174, #2471, #2472, #2678; spec `artifacts/specs/2026-09-29-ac-accepted-and-appeal-fix-design.md`; plan `artifacts/plans/2026-09-29-ac-accepted-and-appeal-fix.md`.
Eval: 77% — artifacts/evaluations/2026-09-29-ac-accepted-and-appeal-fix.md
