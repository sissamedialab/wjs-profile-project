# Author-notes Copy button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Copy button to the "Show author notes" modal that puts the notes on the clipboard as `text/html` + `text/plain`.

**Architecture:** `production_galleyproofing.html` renders the raw notes in a `<template>` next to the modal and passes a button partial through the shared modal's `include_path` (no `wjs-themes` change). A new generic script `clipboard_copy.js`, driven by `data-` attributes, performs the copy and the feedback, and is loaded once on the article status page.

**Tech Stack:** Django templates, Bootstrap 5 + Bootstrap Icons, vanilla JS (Clipboard API), pytest-django.

**Spec:** `artifacts/specs/2026-11-23-author-notes-copy-button-design.md` (issue wjs/specs#3128)

## Global Constraints

- Work only in `wjs-profile-project` (this worktree); `wjs-themes` is read-only, shared `modal.html` is not modified.
- Raw HTML goes into the template unfiltered by `linebreaksbr`; the `<template>` id is `author_notes_source-<version.number>`; the modal id stays `author_notes-<version.number>`.
- Button: `btn btn-sm btn-outline-secondary`, icon `bi-clipboard` → `bi-clipboard-check` while "Copied"; "Copied" shown ~2 s (2000 ms).
- Button only rendered inside the `{% if version.galleyproofing.notes %}` block; no other modal changes.
- All new user-facing strings wrapped in `{% translate %}`.
- Templates: 2-space indentation, djlint `django` profile (`.claude/rules/templates-django.md`). Code style per `.claude/rules/code-style-*.md`.
- Tests are run from `janeway/src` (`.claude/rules/tests.md`). **Prerequisite:** confirm which `wjs` the venv imports (`python -c "import wjs; print(wjs.__file__)"` from `janeway/src`); if it is the main checkout and not this worktree, run pytest with `PYTHONPATH` pointing at this worktree, otherwise tests exercise the wrong code. Ask before running anything outside this worktree if that requires a path change.

## Review Focus

- Two versions on one page: each button must copy its own version's notes (unique ids, no cross-reference).
- Notes containing `&`, `<`, `$\alpha_s$`, accents: plain text must come out unescaped and unchanged; HTML flavour must keep the stored markup.
- Lists: stored `<ul><li>` must stay one list in the HTML flavour (no `<br>` injected).
- Clipboard API or `ClipboardItem` missing/rejected (HTTP, old Firefox): inline message, no JS exception, button state restored.
- Double click while "Copied" is showing: label/icon must not get stuck or flicker permanently.
- Legacy notes stored as plain text with raw newlines (no tags): plain-text flavour should still keep line breaks (check manually; see Task 2).

---

### Task 1: Server-side markup (button partial, source `<template>`)

**Files:**
- Create: `wjs/plugins/wjs_review/templates/wjs_review/details/elements/author_notes_copy_button.html`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/details/elements/production_galleyproofing.html` (the `{% if version.galleyproofing.notes %}` block)
- Test: `wjs/plugins/wjs_review/tests/test_author_notes_copy_button.py`

**Interfaces:**
- Consumes: `author_notes_id` (already set in `production_galleyproofing.html`), shared modal `include_path` param.
- Produces: a `<button type="button" data-copy-source="#author_notes_source-<n>" ...>` with `data-copy-label`, `data-copied-label`, `data-copy-error`-style attributes read by Task 2, plus a hidden `role="alert"` message element inside the partial; and `<template id="author_notes_source-<n>">` containing `version.galleyproofing.notes|safe` (no `linebreaksbr`).

- [ ] **Step 1: Write failing tests** in `test_author_notes_copy_button.py`, rendering the status page (`reverse("wjs_article_details", kwargs={"pk": ...})`, see `test_production.py:607` for the pattern and `stage_proofing_article` / `assigned_to_typesetter_proofs_done_article` fixtures) as the typesetter after setting `GalleyProofing.notes`:
  - `test_copy_button_rendered_with_notes`: response HTML contains `data-copy-source="#author_notes_source-1"` inside `#author_notes-1`'s `.modal-body`, before the notes text.
  - `test_source_template_holds_raw_notes`: `<template id="author_notes_source-1">` contains the exact stored HTML (e.g. `<ul><li>a</li></ul>` and `&amp;` kept as stored) with no `<br>` added.
  - `test_no_button_without_notes`: with empty notes, neither the button nor the template appears.
  - `test_ids_unique_per_version`: two versions with notes → two buttons/templates with distinct ids pointing at their own source.
  - `test_button_available_to_author_and_supervisor`: same button for the author and for the supervisor roles.
  - `test_no_copy_button_on_other_modals`: a reviewer-report modal on the decision page has no `data-copy-source`.
- [ ] **Step 2: Run** `pytest wjs/plugins/wjs_review/tests/test_author_notes_copy_button.py` (from `janeway/src`, see prerequisite) — expected FAIL (no button).
- [ ] **Step 3: Implement** the partial (button with clipboard icon, translated labels as data attributes, hidden alert span) and wire `include_path="wjs_review/details/elements/author_notes_copy_button.html"` plus the `<template>` after the modal include in `production_galleyproofing.html`.
- [ ] **Step 4: Run** the new tests — expected PASS; also run `pytest wjs/plugins/wjs_review/tests/test_production.py` for regressions.
- [ ] **Step 5: Commit** (per commit strategy chosen in flow step 4).

### Task 2: Clipboard script and loading

**Files:**
- Create: `wjs/plugins/wjs_review/static/js/clipboard_copy.js`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/details/articleworkflow_detail.html` (`{% block js %}`, add one `<script src="{% static "js/clipboard_copy.js" %}">`; load `static` in the template if missing)
- Test: add to `test_author_notes_copy_button.py`: `test_status_page_loads_clipboard_script_once`

**Interfaces:**
- Consumes: Task 1's button/template ids and `data-` attributes.
- Produces: delegated `click` handler for `[data-copy-source]`, exposed as function `copyFromSource(button): Promise<void>` (name free for builder; keep it generic, no author-notes specifics).

- [ ] **Step 1: Write failing test** `test_status_page_loads_clipboard_script_once` (script tag present exactly once on the page with two versions). Run → FAIL.
- [ ] **Step 2: Implement** `clipboard_copy.js`:
  - HTML flavour = `source.innerHTML.trim()` of the `<template>` (via the selector in `data-copy-source`); empty → do nothing.
  - Plain flavour = clone `template.content` into a div appended to `document.body`, positioned off-screen but `display:block` (the technique in `copyReviewerReport`, `report_editor.js:52-62`), read `innerText.trim()`, remove it. If the source holds no block-level elements and contains raw `\n`, convert newlines to line breaks first so legacy plain-text notes keep their lines.
  - `navigator.clipboard.write([new ClipboardItem({"text/html": Blob, "text/plain": Blob})])`; if `ClipboardItem` is unavailable fall back to `navigator.clipboard.writeText(plain)`; if the Clipboard API is missing or any call rejects, show the hidden alert message and leave the button in its "Copy" state.
  - On success swap icon (`bi-clipboard` ↔ `bi-clipboard-check`) and label to the "Copied" label for 2000 ms; clear any previous timer before setting a new one so double clicks cannot stick.
- [ ] **Step 3: Run** the status-page test → PASS.
- [ ] **Step 4: Manual verification on a running instance** following the issue's T-1…T-5 (Playwright mode per flow step 5): paste into a rich-text app and a plain-text editor, two versions on one paper, other roles, no-notes paper, another journal. Record results for the MR.
- [ ] **Step 5: Commit.**

### Task 3: Translations and docs

**Files:**
- Modify: locale files only if the repo's workflow expects `.po` updates in feature MRs (`wjs/locale/*/LC_MESSAGES/django.po` appear to be archived in bulk, see `git log` on them — confirm; otherwise leave to the translation workflow and mention it in the MR).
- Modify: none else expected; `doc-sync` at wrap-up decides.

- [ ] **Step 1: Check** how earlier template-only string additions were handled (`git log -p -S"Show author notes" -- wjs/locale`), follow that precedent.
- [ ] **Step 2: Run** `pre-commit run --all-files` (djlint) on the touched files; fix findings.
- [ ] **Step 3: Commit** if anything changed.
