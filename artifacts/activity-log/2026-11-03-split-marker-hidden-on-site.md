## 2026-11-03 — Hide the `[...]` split marker in messages shown on the site
**What:** System messages and reminders shown on the site no longer display the literal `[...]`; the email teaser no
longer crashes when a body contains the marker more than once.
**Why:** wjs/specs#3235 — JQuant saw `[...]` in messages on the site and reported it as leftover placeholders. It is
`Message.SPLIT_MARKER`, used on purpose in settings/reminder templates to cut the email teaser before a "(read
more...)" link (commit `4ca913a5`, Aug 2024); only emails were ever taught to handle it, so the site printed the raw
body (note 78717: "not placeholder but split markers, the rendering issue must be investigated").
**Decisions:** Strip at display time (`Message.display_body`, `Reminder.display_body`, one `Message.strip_split_marker`
helper), so stored messages are fixed with no data migration and settings stay untouched. Only `SYSTEM` messages are
stripped, mirroring the email split; user messages, notes and hijack notices keep `[...]` (a person may type it to
mark an omission in a quote). The site shows the full text — it is the "read more" destination, so no teaser/expand
there. `render_message` now uses `partition` (teaser stops at the first marker). Eval: 83% —
artifacts/evaluations/2026-11-03-split-marker-hidden-on-site.md. e2e: no interactive surface; Playwright skipped by
choice. doc-sync: no drift.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | builder check (general-purpose, Sonnet) | ≈53k | ≈1m |
| Implementation | superpowers:executing-plans (inline, main session) | — | ≈15m |
| Review | final whole-branch review (general-purpose, Opus) | ≈83k | ≈1m |

**Considered & dropped:** Removing `[...]` from the templates (it is the intended email teaser cut); showing a
teaser + expand on the site (double click to reach the text the user came for); documenting the marker convention in
the plugin `CLAUDE.md` (declined by the developer).
**Follow-ups:** Deferred minors from the final review — the email subject / `notification_line` fall back to the raw
body (`body[:20]` / `body[:111]`) and could show `[...]` for a SYSTEM message without subject; the forward form
prefills the raw `original_message.body` (unreachable today, forwarded messages are USER). Local env: the janeway
virtualenv's `pytest_freezegun` imports `distutils` (missing on Python 3.13), tests need `-p no:freezegun`.
**Refs:** wjs/specs#3235; spec artifacts/specs/2026-11-03-split-marker-hidden-on-site-design.md; plan
artifacts/plans/2026-11-03-split-marker-hidden-on-site.md.
