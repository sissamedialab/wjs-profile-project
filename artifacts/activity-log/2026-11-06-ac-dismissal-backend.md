## 2026-11-06 — AC dismissal backend (users can dismiss and restore attention conditions)
**What:** Backend part of AC dismissal: a new `DISMISSED` status (migration `0024`) that survives re-evaluation;
resolvers that also close dismissed rows; per-user EO visibility; an owner-only toggle (logic + form + HTMX view
`toggle-attention-condition`); and a read path (`ac_service.ArticleAttention`, `article_attention` filter, listing
data limited to the current page) for the frontend. A minimal panel is shown on the article page only with
`?ac_panel=1`.
**Why:** wjs/specs#3272. The UI waits for the design in #3367, so the backend is delivered first, giving the
frontend all the data it needs and a toggle endpoint that can already be tested.
**Decisions:**
- One reused row per `(article, user, code)`: `upsert_ac` keeps DISMISSED and refreshes message/priority, and
  re-activates RESOLVED rows. AC-4 ("new AC") means the code stopped applying and then applied again, with no
  per-trigger tracking.
- EO members see their own rows; the EO system user's row is shown only for `HAS_UNREAD_MESSAGE` or, read-only,
  when the member has no row of their own for that code. This fallback is beyond the issue text and still waits
  for Iacopo's answer (note 81236).
- The toggle view always returns the `#attention-panel` container, even when it is empty, not an empty response.
  This keeps later HTMX swaps possible, and it already supports #3367's "variant 2" (when nothing is active, the
  panel shows the dismissed ACs so they can be restored): `has_any` includes dismissed ACs.
- The typesetter-late and author-proofing-late evaluators now decide over all assignments before writing. Before,
  a dismissal flipped back to active with a second round, and a late round could be resolved by a later round that
  was not late.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | builder-check subagent (brainstorming) | ≈107k | ≈2m |
| Implementation | superpowers:executing-plans (native, in session) | — | — |
| Review | final whole-branch reviewer (opus) | ≈130k | ≈3m |

**Considered & dropped:** an empty HTMX response to hide the panel (DET-3/DET-10): the outerHTML swap would remove
the target, and #3367 now keeps the panel for dismissed ACs. Showing the EO system row only for unread messages, as
the issue says: EO members without a journal `AccountRole`, or added since the last rebuild, would see no normal
ACs at all.
**Follow-ups:**
- Gotcha: the migration was first numbered `0022`, then `0023`, and each time clashed with a migration that landed
  on `wjs-develop` in the meantime (`0022_articleworkflow_production_flag_iop_ready`, then
  `0023_editor_assignment_signature_role`); it is now `0024`. Tests skip migrations, so only the new
  `tests/test_migrations_graph.py` catches this. Re-check the latest migration right before pushing.
  `makemigrations --check` was not run.
- Deferred review minors: the toggle error is invisible when the panel is empty (error block inside `has_any`);
  `ArticleDetails` computes `attention` on every render; `upsert_ac`'s two writes are not in one atomic block; the
  toggle view's workflow lookup is not scoped to `request.journal`.
- Separate issue to open: audit of resolve call sites (rows left over from earlier states, users who lose and
  regain a role).
- Frontend: replace the minimal panel and the `?ac_panel=1` hook, rewrite `lists/elements/attention.html`, add
  `aria-label`s with the condition text (A11Y-8).
- e2e: the HTMX toggle would normally get Playwright coverage; skipped by choice at flow start. Manual functional
  steps 6, 7, 9, 11, 12 and 13 passed on the local DB (JCOM article 1516 / workflow 74; test message 100733 added
  for editor 83).
**Refs:** wjs/specs#3272, #3367, #3182; spec `artifacts/specs/2026-11-04-ac-dismissal-backend-design.md`; plan
`artifacts/plans/2026-11-05-ac-dismissal-backend.md`.
Eval: 78% — artifacts/evaluations/2026-11-06-ac-dismissal-backend.md
