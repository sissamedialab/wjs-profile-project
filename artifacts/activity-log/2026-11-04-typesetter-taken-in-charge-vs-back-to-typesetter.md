## 2026-11-04 — Typesetter: "Taken in charge" vs "Back to typesetter" (specs#3120)
**What:** Papers in `TypesetterSelected` now show "Taken in charge" (first typesetting round) or "Back to typesetter" (later rounds) everywhere the state label appears, including the G8 API `status.name`. Each label has its own badge colour, added in wjs-themes. The typesetter "Working on" and "Pending issues" rows show the production version (`<pk>/v<round>`).
**Why:** Typesetters need to tell fresh work from proofs coming back in order to plan, and on high-volume journals like JCAP this is mandatory. The G8 serializer (!1503) was already written expecting the distinction.
**Decisions:**
- Display-only `ReviewComputedStates` computed from the latest `TypesettingRound` in `state_value`. No FSM, queryset or migration change. Round 1 or no round means taken in charge; round > 1 means back to typesetter.
- Kept the `v` in the version (`3613/v1`) to match the EO production list (user decision).
- Colours: `#fb7185`, dark text, for taken in charge (6.0:1). `#991b1b`, white text, for back to typesetter (8.3:1). Both are to be validated on the test environment. `JCOM-theme` no longer exists, so only `wjs-bootstrap/_badges_colors.scss` changes.
- The `wjs-themes` pin in `setup.cfg` is not bumped here; that happens at release (user decision). The themes MR must ship together with or before this one.
- No `.po` updates: the existing state labels aren't translated either.
- Fixed `assigned_to_typesetter_proofs_done_article`, which passed the wrong fixture into its helper. It was unused until now.
- Code-eval: 78%, report at `artifacts/evaluations/2026-11-04-typesetter-round-labels.md`.
- E2e: skipped (Playwright mode c, and no interactive surface was touched).

**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | builder check (general-purpose) | ≈91k | ≈1m |
| Implementation | superpowers:executing-plans (inline, no subagents) | — | — |
| Review | final whole-branch reviewer (Opus) | ≈92k | ≈1m |

**Considered & dropped:**
- A new stored FSM state: too invasive for a display distinction.
- Running `build_assets`: it writes into Janeway's static dir, so the SCSS compile was checked in memory with libsass instead.

**Follow-ups (deferred reviewer minors):**
- One extra `TypesettingRound` query per row. Consider a shared latest-round helper for `state_value` and `versioned_number`.
- List filter dropdowns still say "Typesetter selected".
- "Pending issues" rows for papers without a typesetting round show the review version.
- Add a ReadyForPublication raw-state test.
- Wrap the `test_api` second-round cleanup in `try/finally`.

**Gotcha:** after pulling `wjs-develop`, `--reuse-db` kept a stale `brake_on` check constraint, which failed `test_journal_editors_unified_label_for_eo_disabled[-1-True]`. Running with `--create-db` fixes it.
**Refs:** specs#3120. Spec: `artifacts/specs/2026-11-04-typesetter-taken-in-charge-vs-back-design.md`. Plan: `artifacts/plans/2026-11-04-typesetter-taken-in-charge-vs-back.md`.
