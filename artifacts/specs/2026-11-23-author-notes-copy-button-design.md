# Copy button on the "Show author notes" modal — design

Status: draft, awaiting approval
Issue: wjs/specs#3128 (MR must use `main: #3128`, no closing keywords)
Size: small change (estimate 4h)

## Intent

During proofs the typesetter opens the author notes, selects all, copies and pastes
locally. Selections miss text or catch page content. A **Copy** button at the top of the
modal body copies exactly the notes in one click.

## Goals

- A Copy button above the notes in the author-notes modal, shown wherever the modal is
  shown (Production card, each version in the production accordion; typesetter,
  supervisor, author). One template change covers all of them
  (`production_galleyproofing.html`).
- Clipboard gets two formats: `text/html` (stored notes, formatting preserved) and
  `text/plain` (paragraphs, line breaks, list items on separate lines; no tags, no
  `&amp;`). Nothing outside the notes is copied.
- Each modal copies only its own version's notes.
- Feedback: icon + label switch to "Copied" for ~2 s, then back to "Copy". If the browser
  refuses, a short message asks the user to select the text manually.
- Small button with a Bootstrap clipboard icon, consistent with the WJS Bootstrap theme.
- New strings (Copy, Copied, error message) translatable.

## Non-goals / anti-goals

- No Copy button on other modals (reviewer reports, cover letters, LaTeX reports) and no
  change to the shared `wjs-themes` `modal.html`. The helper is reusable so they can opt in
  later, in a new issue.
- No file download of the notes; annotated files and proofed galleys untouched.
- Failure mode to avoid: copying the modal's rendered HTML (`linebreaksbr` puts `<br>` inside
  lists and splits them in rich editors) or the button label.

## Constraints

- The notes are not wrapped in a container in the shared modal, and `modal_body` gets
  `|safe|linebreaksbr`. `include_path` is rendered before `modal_body` inside `.modal-body`.
- `report_editor.js` is loaded only on `decision.html`; the status page needs its own script.
- `innerText` ignores newlines on hidden elements (#2773); the plain-text path must read
  from a visible, laid-out element.
- Clipboard API needs HTTPS (all WJS environments have it).

## Approaches considered

1. **`<template>` with raw notes + `include_path` button + generic JS (chosen).** Raw
   `version.galleyproofing.notes|safe` goes into a `<template data-author-notes-source>` (no id, see below)
   next to the modal. The button is injected via `include_path`. HTML flavour copies the
   template's stored HTML; plain-text flavour clones it off-screen and reads `innerText`
   (same technique as `copyReviewerReport`). Trade-off: notes appear twice in the DOM
   (modal and template), same cost as the existing reviewer-report pattern. `wjs-themes`
   untouched.
2. **Opt-in `copy_button` parameter in the shared `wjs-themes` modal.** Cleaner for later
   reuse, but needs a coordinated change and release of a second repo and touches the
   template used by every modal. Rejected for a 4h change.
3. **Wrap notes in a container in the modal and copy its `innerHTML`/`innerText`.**
   Needs the shared template to change, and `innerHTML` carries the `linebreaksbr`
   `<br>`-in-list problem. Rejected.

## Design (decided)

- `production_galleyproofing.html`: pass `include_path` pointing to a new element template
  `wjs_review/details/elements/author_notes_copy_button.html`; add the `<template>`
  (id built from `version.number`, like the modal id) after the modal include.
- The button template renders a Bootstrap `btn btn-sm btn-outline-secondary` with
  `bi-clipboard` icon and a label, carrying `data-copy-source="template[data-author-notes-source]"`, resolved inside the closest `[data-copy-root]` ancestor (the wrapper that holds the modal and the `<template>`)
  (and data attributes for the translated labels).
- New static script `wjs_review/static/js/clipboard_copy.js`: one delegated click handler
  on `[data-copy-source]`; builds a `ClipboardItem` with `text/html` and `text/plain`, calls
  `navigator.clipboard.write()`; toggles the icon/label to "Copied" for 2 s; on rejection or
  missing API shows the manual-select message. Loaded where the status page loads its
  scripts (to be located in the plan; included once, not per modal).
- Translations: add strings to the plugin's locale files.

## Left to the builder

- Exact template/script file names, how the script is included on the status page, how the
  failure message is surfaced (inline text in the modal vs. toast, whichever the theme
  already uses), icon choice among the Bootstrap Icons.
- Tests: Python template-render tests (button present with notes, absent without, source
  `<template>` holds raw HTML, ids unique per version, no button on other modals); the
  clipboard behaviour itself is covered by the functional steps T-1..T-5 of the issue and
  the Playwright decision in the flow.

## Amendment (MR review)

The pending-proofs version is rendered twice on the same page (version accordion and the Production action card),
so an id built from `version.number` was duplicated and `document.querySelector` always picked the first one. The
source `<template>` therefore has no id: the script looks for it inside the button's closest `[data-copy-root]`, so
each render copies its own. The modal id and the `id="production"` containers were already duplicated before this
change and are left as they are.
