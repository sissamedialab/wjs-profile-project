## 2026-11-13 — Search analysis corrections after review
**What:** Corrected the spec and plan of the fast article search (wjs/specs#3274) after Iacopo's validation on MR
!1632 against upstream Janeway and the fork. Corrects the gotcha in
`2026-11-12-fast-free-text-article-search.md`. No code change.
**Why:** Two statements in the analysis were wrong or incomplete:
- The re-parse of the stored full-text `tsvector` (`to_tsvector(contents::text)`) is fork-specific. Upstream uses
  the custom `core.model_utils.SearchVector`, which skips the cast; the fork replaced it with Django's `SearchVector`.
  The row product and the per-row rank exist upstream too.
- wjs/janeway#38 is not a table-alias problem. On Django 5.2 `core.model_utils.SearchVector` fails with
  `AttributeError: 'F' object has no attribute 'contains_aggregate'`, in a main query and in a subquery, because its
  `set_source_expressions` is a no-op and Django 5.2's `Func.resolve_expression` relies on it.
**Decisions:** "Do not use `core.model_utils.SearchVector`" still holds; only the reason changes. Fork fix tracked
in wjs/janeway#45.
**Refs:** MR !1632 (review note); wjs/specs#3274; wjs/janeway#38, wjs/janeway#45.
