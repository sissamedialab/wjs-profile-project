## 2026-11-19 — EO action to resend a failed production zip to the publisher
**What:** Added a "Resend to publisher" action for the EO, shown while the `PRODUCTION_EXPORT_SEND_FAILED` attention condition still applies. A good resend logs the EO message and resolves the condition; a failed one refreshes it with the new error.
**Why:** JCAP_7961 moved to ready-for-typesetter, the zip send to IOP failed (credentials edited without a restart), and the EO had no way to send it again. Only `export_production_zip --send` existed, which runs silently and leaves the attention condition active.
**Decisions:**
- Kept the failed first send non-blocking (deliberate in `SendProductionXMLToPublisher`): the issue's "block the state change" TODO was dropped in favour of recovery by the EO.
- Visibility condition is "the attention condition still applies" (ACTIVE or DISMISSED), not "always": no permanent button on every IOP paper.
- New logic class wraps the sender (composition) instead of adding a flag to it; the sender only exposes `send_error`.
- The `ValidationError` for a failed resend is raised after the transaction commits, otherwise the rollback erases the refreshed attention condition.
- The review found a 500 once a typesetter is assigned (export mappers accept only the accepted stage). Fixed by forcing the stage in memory and restoring it, as `export_production_zip --force-accepted` does, not by loosening the mappers.
- No action in `Published`: the publication bundle (!1625) supersedes the zip there.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming, writing-plans (inline) | — | — |
| Implementation | superpowers:executing-plans (native, inline) | — | — |
| Review | whole-branch reviewer subagent (fable) | ≈156k | ≈2.5m |

**Considered & dropped:** a button calling `run(silent=True)` (no EO message, attention condition stays); a one-off command for JCAP_7961 only (no retry for next time, the button covers it); blocking the transition on failure.
**Follow-ups:** deferred minors from the review (no lock against double POST, no state guard after `rollback_acceptance`, duplicated settings lookup, 500 on unknown pk, bare asserts in a few tests). Eval: 77% — artifacts/evaluations/2026-11-19-resend-production-zip.md
**Refs:** wjs/specs#3450, related wjs/specs#2972 and wjs/wjs-profile-project!1625.
