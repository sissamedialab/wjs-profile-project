# Fast free-text article search on the public search page

- **Issue:** wjs/specs#3274 ("Profile search performance on some payloads")
- **Related:** wjs/specs#3236 (03/10/2026 scanner incident), wjs/specs#682 (JCOM search slow / exact title not
  found), wjs/specs#364 (search improvements phase 2), wjs/janeway#38 (SearchVectorField behaviour), wjs/janeway#45
  (fix `model_utils.SearchVector` for Django 5)
- **Project:** wjs-profile-project, `wjs/jcom_profile` (Janeway fork is **not** changed)

## Intent

The JCOM search page (`/search/`, `PublishedArticlesListView`) takes 20–40 s on production for any request with a
free-text term (`article_search`). The issue asks whether this is real slowness or the effect of the 03/10 attack
load, and for the low-hanging fix if it is real.

Measured on the local `janeway_dev4` DB (JCOM: 1422 published articles, 392 `PGFileText` rows), no concurrent load,
second of two runs:

| Request (from the 03/10 access log) | Page | Search SQL |
|---|---|---|
| `sort=relevanceHi9…PG_SLEEP(15)…`, empty `article_search` | 0.19 s | not run |
| `article_search=1*1&sort=relevance` | 12.3 s | 12.1 s |
| `article_search=the&…&show=10*1&year=1990` | 6.7 s | 6.6 s |
| `article_search=the&keywords=74<garbage>&…` | 6.6 s | 6.5 s |
| `/search/` (no params) | 0.20 s | — |

The slowness is real and is entirely one SQL query: Janeway's `ArticleSearchManager.postgres_search`. `EXPLAIN
ANALYZE` shows:

- the query LEFT JOINs article → galley → file → `core_pgfiletext` and article → `frozenauthor`, producing
  ~14 000 rows for 1422 articles (article × galley × author);
- for every row it recomputes `to_tsvector` over title, author names, abstract **and** the full text, where the full
  text is the already-stored `tsvector` cast to text and re-parsed (`to_tsvector(contents::text)`). The re-parse
  is fork-specific: upstream Janeway builds the full-text part with its custom `core.model_utils.SearchVector`,
  which skips the cast, while the fork replaced it with Django's `SearchVector` (stale "not compatible with
  django 3.2" TODO). The row product and the per-row rank exist upstream too;
- joins take ~15 ms; the remaining ~12 s is the `ts_rank(...) >= 0.01` filter. `DISTINCT ON (id)` then collapses
  rows, and the result is wrapped in a raw SQL string (`mogrify` + `Article.objects.raw`).

A hand-written prototype that builds **one vector per article** (authors and full text aggregated in subqueries,
stored full-text `tsvector` used as is) returned the same article ids as the current query for `1*1`, `science`
and `the`, in ~0.29 s instead of 6.6–12.7 s.

## Goals

1. Replace the free-text search used by `PublishedArticlesListView` with a WJS implementation that runs as a single
   ORM query and computes one search vector per article.
2. Keep the result set equivalent to the current search (same matching rule), except for the keyword fix in goal 4
   and the full-text false positives removed by using the stored vector as is (see *Full text: stored vector wins*).
3. Apply the view's other filters (year, sections, keywords, date range, identifier, title/abstract icontains,
   journal, stage, published date, pinned exclusion, `exclude_children`) and the ordering in **the same** query —
   no materialised id list, no `pk__in`.
4. Include article keywords in the free-text vector again (weight B). They were dropped by regression in July 2024
   (`6f5a1f67`, "Updates required by changes in 1.6.0": the WJS form sends `keywords__word`, Janeway checks
   `keywords`); the form label still promises keywords ("Search titles, content, keywords, and authors").
5. Remove raw SQL from the search page's request path (no `mogrify`, no `.raw()`, no `%%` doubling, no
   `IndexError` "escape problem" catch). This is the SQL-injection hardening the issue asks for.

## Non-goals

- ORCID search. The WJS form passes `orcid`, Janeway checks `ORCID`, so it never ran; Janeway's lookup would AND an
  exact ORCID match onto every search. Decided: **do not search ORCID**.
- Changes to the Janeway fork (`submission/models.py`, `core/model_utils.py`). Janeway's own `/search/` view is
  shadowed by the WJS URL and is left as is.
- Search redesign: stored/indexed vectors for title/abstract/authors (GIN), boolean operators, multi-author queries
  (#364), language-specific text-search configuration. These go to follow-up issues.
- Other page costs (~170 small queries during template rendering, keyword `Count`/`count()` in `SearchForm.__init__`):
  ~0.15 s total, follow-up issue.

## Anti-goals

- Articles that the current search finds and the new one does not, other than the full-text false positives described
  in *Full text: stored vector wins* and the multi-word rank-threshold cases in *Multi-word queries near the
  threshold*.
- Any user-controlled value reaching SQL other than as a bound parameter.
- Using Janeway's custom `core.model_utils.SearchVector` (wjs/janeway#38). On Django 5.2 it fails, in the main
  query and in a subquery alike, with `AttributeError: 'F' object has no attribute 'contains_aggregate'`:
  `Func.resolve_expression` calls `set_source_expressions`, which the class overrides as a no-op (Django 4.2 assigns
  `source_expressions` in place, so upstream works there). Fix tracked in wjs/janeway#45. Build the search from
  Django expressions instead.

## Behaviour

### Search vector (per article)

| Part | Source | Weight |
|---|---|---|
| Title | `Article.title` | A |
| Keywords | all `keywords__word`, aggregated | B |
| Authors | all `FrozenAuthor` `last_name` + `first_name`, aggregated | B |
| Abstract | `Article.abstract` | C |
| Full text | stored `PGFileText.contents` of all the article's galleys (via `galley__file__text`), aggregated, used as a `tsvector` without re-parsing | D |

- Query: `plainto_tsquery(term)` with the default text-search configuration, as today (`SearchQuery(term)`).
- Match rule: `ts_rank(vector, query) >= 0.01`, as today.
- Missing parts (no authors, no keywords, no galley text) contribute an empty vector, never NULL-out the whole
  vector.

### Full text: stored vector wins (decided)

Today the stored `tsvector` is cast to text and re-parsed (`to_tsvector(contents::text)`). Its text form carries
positions (`'scienc':12,45`), so the re-parse turns position numbers into lexemes and stems already-stemmed words
again — verified locally: `to_tsvector('''scienc'':12,45 ''communic'':3')` →
`'12':2 '3':5 '45':3 'communic':4 'scienc':1`. Numeric terms (`1*1` → `'1' & '1'`, `2019`, …) therefore match
articles by position numbers, not content.

Decision: the stored vector is used **as is** (no re-parse). Articles found today only through position numbers or
double stemming are no longer found; this is a bug fix, not a regression. Re-parsing once per article (strict
"old ⊆ new") was rejected: ~1 s instead of ~0.3 s.

### Multi-word queries near the threshold (decided during implementation)

For a multi-word term `ts_rank` depends on the distance between the matched lexemes. Aggregating all authors into one
vector shifts positions, so an article's per-article rank can differ from the rank of the single article × author ×
galley row the old query happened to keep. Near the `0.01` threshold a few articles cross it in either direction
(local check over three multi-word terms: 4 articles lost, 3 gained not counting keyword matches; e.g. article 314,
"Conceptualizing science communication in flux": ~0.097 → 0.0097). Decision: **accepted**; reported with numbers.
Switching the match rule to "all words present" (`vector @@ query`) or tuning the threshold was rejected for this
change.

### Multiple galleys with text (builder's call)

All the article's galley texts are concatenated into the D part. PostgreSQL caps a `tsvector` at 1 MB; locally the
largest stored vector is ~167 KB (HTML and PDF galleys carry text) and no article has more than one text galley, so
reaching the cap needs 6+ large text galleys on one article. Accepted risk; no special handling.

### Author-only search

When `article_search` is empty and `article_authors` is set (advanced search), the vector is **authors only**
(weight B), same match rule. This mirrors today's `get_author_filter` path.

### Ordering

- `sort` values stay as today: `relevance`, `title`, `-title`, `-date_published`, `date_published` (form default
  `-date_published`; invalid values fall back to the default).
- `relevance` with a search term → order by the per-article rank, descending. `relevance` without a term →
  `-date_published` (unchanged `get_order_by`).
- Ranking differs slightly from today: today the rank comes from an arbitrary row of the article × galley × author
  product (`DISTINCT ON (id) … ORDER BY id`); now it is the rank of the whole article. Accepted.

### No term

Without `article_search`/`article_authors` the view behaves exactly as today (plain filtered list, no full-text
work).

## Constraints

- Article is a Janeway model: WJS cannot add a queryset method to its manager without monkeypatching. Put the search
  in a WJS module as a function/class that takes a queryset and a term and returns an annotated queryset; keep the
  view thin (project rule: `.claude/rules/architecture-django.md`).
- PostgreSQL only (`CORE_FILETEXT_MODEL = "core.PGFileText"`, `ENABLE_FULL_TEXT_SEARCH = True` in
  `wjs/defaults/settings.py`; tests use the same settings).
- No migrations, no new dependencies.
- Existing tests must keep passing: `wjs/jcom_profile/tests/test_search.py` (hackish inputs incl. `%` terms,
  filters, sort), `test_article_links.py::…::test_search` (children not excluded from search), `test_views.py`.

## Verification

- Tests (TDD): match on title, keywords, author name, abstract, full text; author-only search; non-matching term
  returns nothing; stop-word-only term (`the`) returns nothing without error; each sort value; filters combined
  with a term (year, sections, keywords); terms with quotes, `%`, `'`, SQL fragments return 200; article with no
  galleys/authors/keywords is still found by title; the search queryset nested in another query (subquery
  path) compiles and returns the correct rows.
- Not committed: on the local DB, compare result id sets of old vs new search for a handful of terms
  (`1*1`, `science`, `the`, `climate`, an author surname, a keyword, the #682 title "Conceptualizing science
  communication in flux"); record before/after timings for the four logged requests and ordinary searches. Every
  difference must be explained: new − old by keyword matches, old − new by position-number / double-stemming
  false positives (check by matching the term against the article's title, abstract, authors and stored vector).

## Deliverables outside the MR

Drafted in this flow, posted only after the user approves the text:

- Report comment on wjs/specs#3274: per-request timings, where the time goes, "real vs attack load" verdict
  (real; the PG_SLEEP payload never reached the DB because `sort` is whitelisted by the form `ChoiceField` and
  `SORT_KEYS`), proposed target response time, SQL-injection analysis, before/after timings.
- Follow-up issues: stored + indexed vectors (GIN); page query count / form counts; #682 exact-title behaviour if
  still wrong after this change; search syntax (#364).

## Left to the builder

- Module/class naming and exact ORM construction of the aggregated vectors (e.g. `StringAgg` + `to_tsvector` for
  authors/keywords; for full text, an aggregation that yields a `tsvector` without re-parsing, such as
  `string_agg(contents::text, ' ')::tsvector`, which the prototype used).
- Whether to keep `SearchForm.get_search_filters`/`get_author_filter` or simplify them, as long as the view's
  behaviour above holds.
- Target response time number for the report (proposal based on after-timings).
