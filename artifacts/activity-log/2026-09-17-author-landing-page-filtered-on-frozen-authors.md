## 2026-09-17 — Author landing page filtered on frozen authors, not the deprecated M2M

**What:** `/articles/author/<pk>/` was omitting articles — recent ones and co-authored
positions especially — while the advanced author search returned them all. The two
paths share `PublishedArticlesListView` but read different sources: the click-through
filtered `Article.authors`, the search hits `FrozenAuthor` name fields. Switched the
click-through filter to `frozenauthor__author`.

**Why:** `Article.authors` is deprecated in Janeway 1.8 (it raises a
`DeprecationWarning` on every access) and nothing maintains it in the WJS review
workflow. `on_article_submission_start` adds only the submitting user; co-authors
never enter it. Worse, `logic.py:1925` deletes all `FrozenAuthor` rows on revision
confirm and re-snapshots — and Janeway's `remove_author_from_article` pre_delete
signal mirrors each deletion back, so a revision *empties* the legacy M2M outright.
`FrozenAuthor` is the published-state source of truth; `Article.author_accounts` and
~20 call sites in `wjs_review` already use it. `wjs/jcom_profile/views.py` was the
user-facing holdout.

**Decisions:** Filter on `frozenauthor__author` alone rather than union it with
`authors`. The union never loses a row that works today, but submission puts the
*submitting* user into `authors`, so an EO who submitted on an author's behalf would
see those articles on their own author page. Deduplication (`.distinct()`) is applied
only when a multi-valued lookup is actually present — the same queryset chain serves
`/articles/`, where `SELECT DISTINCT` over ~70 Article columns would be paid for
nothing.

**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Review | superpowers:requesting-code-review (general-purpose subagent) | ~125k | ~8m |

Design, implementation and the wrap-up skills ran inline in the main session; no
subagent was dispatched for them.

**Considered & dropped:**
- *Union both fields* — rejected for the EO false-positive above.
- *Backfill the `authors` M2M* — rejected: the field is deprecated and unmaintained,
  so the drift and the bug would return with the next published article.
- *Fixing the three sibling `Article.authors` queries in `wjs_review`* — deliberately
  deferred. The two `.exclude(article__authors=...)` cases fail *open*, so switching
  them would *hide* rows from a dashboard: a different risk profile that deserves its
  own review, not a ride-along on a 2h issue.

**Gotchas worth keeping:**
- The two representations are kept in sync by a *pair* of Janeway signals —
  `backwards_compat_authors` (m2m_changed) and `remove_author_from_article`
  (pre_delete). The legacy-only state is therefore unreachable through the ORM: a
  test that needs it must write `Article.authors.through` directly. Deleting a
  `FrozenAuthor` to construct it does not work — it removes the M2M row too.
- The original pre-merge audit query,
  `Article.objects...exclude(frozenauthor__author__in=F("authors"))`, is **wrong**.
  `exclude()` across a multi-valued relation builds an *uncorrelated* subquery, so it
  only catches articles where no legacy author at all has a frozen counterpart, and
  silently passes the dangerous partial case. Corrected version in the spec.
- The existing `test_filter_articles_by_author` was blind to the bug because
  `_create_published_articles()` calls `authors.add()` then `snapshot_authors()`,
  deriving the frozen rows from the M2M — the two can never diverge in fixtures.
- `pytest` cannot start in the shared janeway virtualenv: a stale `pytest-freezegun`
  (replaced by `pytest-freezer` in `setup.cfg` during the pytest 9 port) imports
  `distutils`, gone in Python 3.13. Every run needs `-p no:freezegun` until someone
  uninstalls it.

**Follow-ups:**
- Run the corrected data audit on production-like data before merge.
- Separate work item for the remaining `Article.authors` sites: `communication_utils.py:126`,
  `views.py:719`/`743`, `signals.py:121` (role-cache receiver, now effectively dead for
  workflow articles — possible stale caches), `logic.py:4981` (benign fallback).
- **Different repo:** `article_documents.html` in both `wjs-themes` themes builds author
  links from the `FrozenAuthor` pk instead of the `Account` pk (`wjs-bootstrap:45`,
  `JCOM-theme:35`) — those links point at the wrong author or 404.
- `MULTI_VALUED_FILTERS` restates filter keys declared in `_get_filters()`; a rename
  would silently disable deduplication. Worth deriving or asserting.
- Uninstall `pytest-freezegun` from the janeway virtualenv.

**Refs:** [specs#3048](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3048);
spec `docs/superpowers/specs/2026-09-17-author-filter-frozen-authors-design.md`;
plan `docs/superpowers/plans/2026-09-17-author-filter-frozen-authors.md`;
Eval: 77% — artifacts/evaluations/2026-09-17-author-filter-frozen-authors.md
