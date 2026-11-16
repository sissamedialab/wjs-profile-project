# Fast Free-Text Article Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public search page's free-text search run as one ORM query with one search vector per article
(≈0.3 s instead of 7–13 s locally), with keywords searched again and no raw SQL in the request path.

**Architecture:** A new WJS module `wjs/jcom_profile/search.py` exposes one function that takes a queryset of
articles and a term and returns it filtered by full-text match and annotated with `relevance`. Authors, keywords and
stored galley full text enter the vector through correlated subqueries, so there is no article × galley × author row
product. `PublishedArticlesListView.get_queryset` calls it instead of Janeway's `Article.objects.search`, and applies
its filters and ordering to the same queryset.

**Tech Stack:** Django ORM, `django.contrib.postgres.search` (`SearchVector`, `SearchQuery`, `SearchRank`),
`django.contrib.postgres.aggregates.StringAgg`, PostgreSQL full-text search, pytest-django.

**Spec:** `artifacts/specs/2026-11-12-fast-article-search-design.md` (read it first; this plan argues from it).

## Global Constraints

- Change only `wjs-profile-project`; the Janeway fork is not modified.
- No migrations, no new dependencies.
- Vector weights: title A, keywords B, authors (`FrozenAuthor.last_name` + `first_name`) B, abstract C, stored
  `PGFileText.contents` of all the article's galleys D.
- Query: `SearchQuery(term)` (= `plainto_tsquery`, default configuration). Match rule: `relevance >= 0.01`.
- Stored full-text vector is used **as is** — never `to_tsvector(contents::text)`.
- Do not use `core.model_utils.SearchVector` (wjs/janeway#38): on Django 5.2 its no-op `set_source_expressions`
  makes it fail with `AttributeError: 'F' object has no attribute 'contains_aggregate'`; fix tracked in
  wjs/janeway#45.
- ORCID is not searched.
- User input reaches SQL only as bound parameters: no `.raw()`, no `mogrify`, no string-built SQL.
- Python style: black/isort/flake8 at line length 119, double quotes, docstrings on public functions
  (`.claude/rules/code-style-python.md`, `.claude/rules/linting.md`).
- Tests run from `janeway/src` (outside this repo — needs the user's go-ahead for this flow):
  `/Users/sfedoruk/.pyenv/versions/janeway/bin/python -m pytest ../../wjs-profile-project/<path> -p no:freezegun`
  (add `--create-db` once if the reused test DB is stale).

## Review Focus

1. Article with no authors, no keywords and no galley text → still found by title (a NULL part must not null the
   whole vector). Test in Task 1: `test_article_without_related_parts_found_by_title`.
2. Search queryset nested in another query (`pk__in=…values("pk")`, `exclude_children`, `.distinct()`) → valid SQL,
   correct rows. Test in Task 1: `test_search_queryset_usable_as_subquery`.
3. Numeric term matching only stored positions (`12`) → no match (re-parse not reintroduced). Test in Task 1:
   `test_stored_vector_not_reparsed`.
4. Terms with `'`, `%`, `;`, `--`, `PG_SLEEP(…)` → 200 and normal results, not 500. Existing
   `test_search.py::test_search_view_resilient_to_hackish_inputs` plus new cases in Task 2.
5. Article with two authors / two keywords / two text galleys → appears **once** in results, paginator count
   correct. Test in Task 1: `test_article_listed_once_with_many_related_rows`.

---

### Task 1: Search function

**Files:**
- Create: `wjs/jcom_profile/search.py`
- Test: `wjs/jcom_profile/tests/test_article_search.py`

**Interfaces:**
- Produces: `search_articles(queryset: QuerySet[Article], term: str, authors_only: bool = False) -> QuerySet[Article]`
  — returns `queryset` annotated with `relevance` (float) and filtered to `relevance >= 0.01`. No ordering applied.
  With `authors_only=True` the vector is the authors part only (weight B).
- Module constant `MIN_RELEVANCE = 0.01`.

Test data: build articles in the test module with an explicit helper (do not reuse `published_articles`, whose
titles `Title{i}` / abstracts `Abstract{i}` would interfere):
`_article(journal, section, title="Plain", abstract="Plain", authors=(), keywords=(), full_texts=())` creating a
published article; authors as `FrozenAuthor(article=…, first_name=…, last_name=…, order=n)`; keywords via
`Keyword.objects.create(word=…)` + `article.keywords.add`; each full text as `File` + galley
(`utils.testing.helpers.create_galley`) + `FileText.objects.create(contents=FileText.preprocess_contents(text))`
with `FileText = swapper.load_model("core", "FileText")` assigned to `file.text`.

- [ ] **Step 1: Write the failing tests** in `test_article_search.py` (all `@pytest.mark.django_db`; `qs` below is
  `Article.objects.filter(journal=journal)`):
  - `test_match_on_each_part` — parametrize over `title` / `keywords` / `authors` (surname) / `abstract` /
    `full_text`; one article carries the word `xylophone` only in that part, another article has none →
    `list(search_articles(qs, "xylophone")) == [target]`.
  - `test_no_match_returns_empty` — `search_articles(qs, "zzzunmatched").exists() is False`.
  - `test_stopword_only_term_returns_empty` — `search_articles(qs, "the").exists() is False`, no exception.
  - `test_authors_only` — article A has `xylophone` in title, article B as author surname →
    `set(search_articles(qs, "xylophone", authors_only=True)) == {B}`.
  - `test_article_without_related_parts_found_by_title` — no authors, keywords, galleys; title `xylophone` → found.
  - `test_stored_vector_not_reparsed` — article whose only full text is `"science communication " * 20` (stored
    positions include 12) and neutral title/abstract → `search_articles(qs, "12").exists() is False`, while
    `search_articles(qs, "communication")` finds it.
  - `test_article_listed_once_with_many_related_rows` — article with 2 authors, 2 keywords, 2 text galleys all
    containing `xylophone` → `search_articles(qs, "xylophone").count() == 1`.
  - `test_relevance_higher_for_title_than_full_text` — A has the word in title, B only in full text →
    `A.relevance > B.relevance` on the annotated rows.
  - `test_search_queryset_usable_as_subquery` —
    `Article.objects.filter(pk__in=search_articles(qs, "xylophone").values("pk"))` evaluates and returns the
    target (full-text match, so the galley subquery is nested).
  - `test_hostile_terms_are_parameters` — parametrize `"o'reilly"`, `"50%"`, `"1*1"`,
    `"')) OR 355=(SELECT 355 FROM PG_SLEEP(15))--"` → `list(search_articles(qs, term))` raises nothing.

- [ ] **Step 2: Run, verify they fail**
  Run: `…pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_article_search.py -p no:freezegun -v`
  Expected: collection error / FAIL — `wjs.jcom_profile.search` does not exist.

- [ ] **Step 3: Implement `search_articles` in `wjs/jcom_profile/search.py`**
  Build each related part as a correlated `Subquery` on `OuterRef("pk")` returning one value per article:
  authors → `StringAgg` of `Concat(last_name, " ", first_name)`; keywords → `StringAgg` of `word`; full text →
  aggregate the stored vectors of `FileText` rows reached through `file__galley__article` into one `tsvector`
  **without** `to_tsvector` (e.g. a `Func`/aggregate expressing `string_agg(contents::text, ' ')::tsvector`, as in
  the spec's prototype). Text parts go through Django's `SearchVector(…, weight=…)`; the full-text part is wrapped
  in `setweight(…, 'D')` directly. Every part is `Coalesce`d to an empty value. Combine with `||`, rank with
  `SearchRank(vector, SearchQuery(term))`, annotate `relevance`, filter `relevance__gte=MIN_RELEVANCE`.
  Docstring explains why subqueries (no row product) and why the stored vector is not re-parsed.

- [ ] **Step 4: Run, verify they pass** (same command). Expected: all PASS.

- [ ] **Step 5: Commit** (per the commit strategy chosen at flow step 4)
  `git add wjs/jcom_profile/search.py wjs/jcom_profile/tests/test_article_search.py`
  `perf(search): add per-article full-text search for published articles`

### Task 2: Use it in the search page

**Files:**
- Modify: `wjs/jcom_profile/views.py` — `PublishedArticlesListView.get_queryset` (≈ lines 806–856), imports
  (`RawQuerySet` import removed if unused).
- Test: `wjs/jcom_profile/tests/test_search.py`

**Interfaces:**
- Consumes: `search_articles(queryset, term, authors_only=False)` from Task 1.

Behaviour to implement (spec *Behaviour* section): start from `self.model.objects.all()`, apply `filters`, the
pinned exclusion, conditional `.distinct()`, `exclude_children` exactly as today; when a term exists, pass the
queryset through `search_articles(…, term, authors_only=<term came from get_author_filter>)`. Ordering: if the term
exists and `get_order_by(term) == "relevance"` → `order_by("-relevance", "-date_published")` (secondary key is a
planner's call for stable pagination; today there is none); otherwise
`order_by(self.get_order_by(term))` (now also with a term). Remove the `Article.objects.search` call, the
`RawQuerySet` branch, the pk list, the `pk__in` re-filter and the `IndexError` catch. `SearchForm` is unchanged.

- [ ] **Step 1: Write the failing tests** in `test_search.py` (reuse `journal`, `client`; build articles with a small
  local helper like Task 1's):
  - `test_search_matches_keyword` — article whose keyword is `xylophone` (not in title/abstract) →
    `client.get(f"/{journal.code}/search/", {"article_search": "xylophone"})` lists it. (Fails today: regression.)
  - `test_search_sort_with_term` — parametrize `title` / `-title` / `date_published` / `-date_published`: two
    matching articles → order of `response.context["articles"]` follows the sort.
  - `test_search_relevance_order` — title match ranked before full-text-only match with `sort=relevance`.
  - `test_search_does_not_use_raw_sql` — `with CaptureQueriesContext(connection) as q: client.get(…term…)`;
    assert `not any(") AS search" in x["sql"] for x in q.captured_queries)` (the raw wrapper Janeway builds).
  - Extend `test_search_view_resilient_to_hackish_inputs` with
    `"/search/?article_search=1*1&sort=relevance"`,
    `"/search/?article_search=o%27reilly"`,
    `"/search/?abstract=on&article_search=&sort=relevanceHi9BvUPf%27))%20OR%20355=(SELECT%20355%20FROM%20PG_SLEEP(15))--&title=on"`.

- [ ] **Step 2: Run, verify the new ones fail** (keyword and raw-SQL tests at least):
  `…pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_search.py -p no:freezegun -v`

- [ ] **Step 3: Implement** the `get_queryset` change described above.

- [ ] **Step 4: Run the search-related suites, verify all pass:**
  `…pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_search.py
  ../../wjs-profile-project/wjs/jcom_profile/tests/test_article_search.py
  ../../wjs-profile-project/wjs/jcom_profile/tests/test_views.py
  ../../wjs-profile-project/wjs/jcom_profile/tests/test_article_links.py -p no:freezegun -n4`
  Expected: all PASS (incl. existing `test_views.py::test_search` and the article-links search test).

- [ ] **Step 5: `pre-commit run --all-files`**, then commit
  `git add wjs/jcom_profile/views.py wjs/jcom_profile/tests/test_search.py`
  `perf(search): use per-article full-text search on the search page`

### Task 3: Verification on the local DB and report drafts (not committed)

**Files:** scratchpad only (`/private/tmp/claude-501/-Users-sfedoruk-Projects-sissa/810355a9-a4db-4a12-beff-2db5d5e06f25/scratchpad/`).

- [ ] **Step 1: Result-set comparison.** On `janeway_dev4`, JCOM, for `1*1`, `science`, `the`, `climate`, one author
  surname, one keyword, and `Conceptualizing science communication in flux`: old ids =
  `Article.objects.search(term, <view's filters dict>, sort="relevance", site=journal)`, new ids =
  `search_articles(<published JCOM qs>, term)`. Every difference explained per spec *Verification* (new − old by
  keywords; old − new by position-number / double-stemming false positives). Unexplained difference → stop and
  report to the user.
- [ ] **Step 2: Timings — local DB only** (`janeway_dev4`; decided by the user: no `wjs-dev` measurements in this
  flow, the report states this). Request set, through the Django test client on the JCOM host:
  - the four logged requests from the issue's Context, verbatim;
  - `/search/` with no params;
  - ordinary searches per the issue's functional test step 2: common word (`article_search=science`), author name
    (`article_search=<a JCOM surname>`), term + keyword filter (`article_search=science&keywords=<pk>`), term +
    year filter (`article_search=science&year=<recent year>`), author-only advanced search (`article_authors=…`).
  Each request: 1 warm-up + 5 runs; record median / min / max total time, SQL time and query count (via
  `CaptureQueriesContext`); Python time = total − SQL. Run once on `origin/wjs-develop` code (before) and once on the
  branch (after), same DB, no concurrent load.
- [ ] **Step 3: Query plans.** `EXPLAIN (ANALYZE, BUFFERS)` of the search query for `1*1` before and after; keep the
  top nodes, row counts and execution time for the report.
- [ ] **Step 4: Draft the report** (issue comment, English): per-request timings and where time goes; verdict (real
  slowness, not attack load; PG_SLEEP never reached the DB); proposed target response time; SQL-injection analysis
  (sort whitelisted twice; search term previously inlined via `mogrify`, now bound parameter); before/after table;
  #682 title result; note that numbers come from a local DB (JCOM 1422 published articles, 392 full texts), not
  `wjs-dev`, and that production is ≈3× slower before the fix per the access log.
- [ ] **Step 5: Draft follow-up issues** (title + short description each): stored + GIN-indexed vectors; page
  query count / `SearchForm` counts; #682 exact-title behaviour if still wrong; search syntax (#364). Show drafts to
  the user; nothing is posted without explicit approval.
