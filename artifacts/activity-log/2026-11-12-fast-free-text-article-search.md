## 2026-11-12 — Fast free-text article search on the search page
**What:** The public search page no longer uses Janeway's raw `postgres_search`; `wjs/jcom_profile/search.py`
builds one search vector per article in a single ORM query, and the view applies filters and ordering to it. Locally
`1*1` went from 12.8 s to 1.0 s, filtered searches to 0.2–0.3 s.
**Why:** wjs/specs#3274 — 20–40 s responses during the 03/10 scanner incident. Profiling showed real slowness, not
attack load: article × galley × author row product (14k rows for 1422 articles) with the stored full-text
`tsvector` re-parsed per row.
**Decisions:** Fix in WJS, not in the Janeway fork. Keep the `ts_rank >= 0.01` rule. Use the stored vector as is,
which drops position-number false positives such as `2019` matching by position. Accept multi-word rank-threshold
differences (a few articles each way). Search keywords again; this was a July 2024 regression from the `keywords__word`
key mismatch. Do not search ORCID. Make ordering total with a `pk` tie-breaker. Playwright skipped (backend-only).
Gotchas:
- Janeway's `core.model_utils.SearchVector` breaks table aliases in subqueries (wjs/janeway#38), so it is not used.
- Before this change, sorting with a term was silently lost by the view's `pk__in` re-filter.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Design | general-purpose builder check (superpowers:brainstorming) | ≈89k | ≈2m |
| Review | code reviewer, opus (superpowers:requesting-code-review) | ≈80k | ≈3m |

**Considered & dropped:**
- One-line fix in the fork (stored vector only): ≈10× instead of ≈13–60×.
- Strict "old ⊆ new" with a per-article re-parse: ≈1 s floor.
- `vector @@ query` match rule or a different threshold: a new semantics needing its own check.
**Follow-ups:**
- Compute rank once (now evaluated in WHERE, ORDER BY and COUNT, ≈3× headroom).
- Stored and GIN-indexed vectors.
- Page query count.
- Exact-title boost and search syntax (#682, #364).
- Report and follow-up issues on wjs/specs#3274, after approval.
**Eval:** 78% — artifacts/evaluations/2026-11-12-fast-article-search.md
**Refs:** wjs/specs#3274; spec `artifacts/specs/2026-11-12-fast-article-search-design.md`; plan
`artifacts/plans/2026-11-12-fast-article-search.md`.
