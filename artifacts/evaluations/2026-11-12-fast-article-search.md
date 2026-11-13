# Evaluation — fast-article-search

- **Date:** 2026-11-12
- **Branch:** bugfix/issue-3274-fast-article-search (working tree vs origin/wjs-develop 306a7e91)
- **Task:** wjs/specs#3274
- **Coverage:** full — `search.py`, `views.py` (`PublishedArticlesListView.get_queryset`), `tests/conftest.py`,
  `tests/test_article_search.py`, `tests/test_search.py`

## Scores
| Dimension | Score | Weight | Key evidence |
|---|---|---|---|
| Functionality | 4 | 20 | Spec goals 1–5 met; local run 12.78 s → 1.04 s (`1*1`), filtered searches 0.2–0.3 s; old-vs-new id comparison explained (keywords, position false positives, accepted multi-word threshold cases); ~3× headroom left (rank in WHERE/ORDER BY/COUNT) |
| Testing | 4 | 15 | 21 new tests, RED observed for each (import error, keyword/sort/raw-SQL failures, order/tie-breaker); mutation check fails `test_stored_vector_not_reparsed`; suites 106 passed; `test_hostile_terms_are_parameters` asserts only `isinstance(list)` |
| Security | 4 | 15 | Raw SQL/`mogrify`/`.raw()` removed from the request path (`views.py` get_queryset); term bound to `plainto_tsquery`; hostile-term tests; Janeway's raw `postgres_search` still in the fork, unused here |
| Code quality & best practices | 4 | 15 | `pre-commit run --all-files` clean; small focused module; tests import private helpers `_authors_text`/`_keywords_text` |
| Maintainability & flexibility | 4 | 15 | One entry point `search_articles(queryset, term, authors_only)`; weights/threshold named (`MIN_RELEVANCE`); view only composes querysets |
| Error handling | 3 | 10 | Silent `IndexError` catch removed; no new failure paths, but the accepted 1 MB `tsvector` cap on aggregated galley texts would surface as a 500 |
| Documentation | 4 | 10 | Docstrings on `search_articles`, `StoredVectorAgg`, `WeightedStoredVector`, helpers; spec/plan in `artifacts/`; no prose docs reference the search internals |

## Recommendations
- Error handling: if production has articles with many text galleys, cap or pick galley texts before aggregation (follow-up with the stored-vector issue).

## Total
**78%** — correct, safe and measurably faster; remaining work (single-pass rank, indexed vectors) is scoped as follow-ups.
