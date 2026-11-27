## 2026-11-23 — Copy button on the author notes modal
**What:** Added a Copy button at the top of the "Show author notes" modal that puts the notes on the clipboard as `text/html` and `text/plain`, via a new generic `clipboard_copy.js` driven by `data-copy-source`.
**Why:** Typesetters select-all/copy/paste the notes by hand and lose text or catch page content (wjs/specs#3128).
**Decisions:** Raw notes go in a `<template id="author_notes_source-<n>">` and the button is injected through the shared modal's `include_path`, so `wjs-themes` is untouched. The modal HTML is not copied because `linebreaksbr` splits lists. Plain text comes from an off-screen clone read with `innerText` (same trick as `copyReviewerReport`). Translations left to the bulk `.po` refresh.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | superpowers:brainstorming + writing-plans | ≈45k | ≈25m |
| Implementation | superpowers:executing-plans (inline) | ≈60k | ≈30m |
| Review | whole-branch reviewer subagent (opus) | ≈92k | ≈2m |
| Review | code-eval, doc-sync | ≈15k | ≈5m |

**Considered & dropped:** An opt-in `copy_button` parameter in the shared `wjs-themes` modal (second repo for a 4h task); wrapping the notes in a container in the shared modal (still carries the `<br>`-in-lists problem).
**Follow-ups:** Run the issue's manual steps T-1..T-5 (Playwright skipped, browser behaviour unverified). Deferred minors: error message never cleared, misleading error for notes with no visible text, narrow `BLOCK_SELECTOR`, fragile `_modal_body` test regex, supervisor path untested.
**Eval:** 71% — artifacts/evaluations/2026-11-23-author-notes-copy-button.md
**Refs:** wjs/specs#3128 (MR must use `main: #3128`, no closing keywords).
