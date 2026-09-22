# Design: filter the author landing page on frozen authors

**Issue:** [specs#3048 — Problems filtering by author in Vetrinetta](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3048)
(Priority 1, Size 2h, Sprint 26W36)

**Status:** Root-cause analysis and fix approach, approved before implementation.

## Context

Reported symptom: clicking an author's name on jcom.sissa.it lands on
`/articles/author/<pk>/` and the resulting list omits some of that author's
published articles — apparently the most recent ones. Searching for the same
author through the advanced search returns everything.

The report reads as one feature behaving inconsistently. It is not. The two
paths are served by the same view, `PublishedArticlesListView`
(`wjs/jcom_profile/views.py`), but they query two different data sources:

| Path | Mechanism | Source of truth |
| --- | --- | --- |
| `/articles/author/<pk>/` (click-through) | ORM filter `Article.objects.filter(authors=<pk>)`, built in `_get_filters()` | `Article.authors` (M2M to `Account`) |
| Advanced search `?article_authors=<name>` | full-text search via `Article.objects.search()`, filters from `SearchForm.get_author_filter()` | `FrozenAuthor` name fields |

`_get_filters()` even documents the split in its own docstring: *"authors are
excluded from this search because we will use full-text search on the authors'
fields alone to be able to match full names, initials, etc."* — but the
click-through filter it builds still keys on `Article.authors`.

## Root cause

`Article.authors` is deprecated in Janeway 1.8 and is no longer maintained for
articles published through the WJS review workflow.

In Janeway core (`src/submission/models.py`):

- the field carries the comment *"The authors field is deprecated. Use
  FrozenAuthor or author_accounts instead."*;
- `Article.__getattribute__` raises a `DeprecationWarning` on every access to
  `authors`;
- the documented replacement, `Article.author_accounts`, is a property defined
  as `Account.objects.filter(frozenauthor__article=self).order_by("frozenauthor__order")`.

On the WJS side the field is populated in exactly two places:

1. **Submission.** `on_article_submission_start`
   (`wjs/plugins/wjs_review/events/handlers.py`) calls
   `submission_logic.add_user_as_author(request.user, article)` — it adds the
   *submitting user only*, never the co-authors. The accompanying comment is
   explicit: *"FIXME: Article.authors and related methods has been deprecated.
   To reduce the impact of the migration we are going to keep its usage,
   removing in the future."*
2. **The wjapp importers.** `import_articles_from_wjapp*.py` call
   `article.authors.clear()` / `.add()` and `sync_frozen_authors_with_authors()`,
   which do keep both sides consistent.

Meanwhile the published-state author record is built independently:
`ensure_snapshot_authors` (same module) is registered for
`ON_ARTICLE_ACCEPTED` and `ON_ARTICLE_PUBLISHED` and iterates
`article.author_accounts` to create `FrozenAuthor` rows.

So `Article.authors` is a partial, stale snapshot: complete for imported
legacy articles, and reduced to "whoever pressed submit" for articles that came
through the review workflow. That is precisely the reported shape — recent
articles missing, co-authors missing worst of all, advanced search unaffected
because it reads frozen author names.

### The two representations are one-way synced

Janeway 1.8 keeps the deprecated M2M in step with `FrozenAuthor` through a pair
of signals in `src/submission/models.py`:

- `backwards_compat_authors` (`m2m_changed` on `Article.authors.through`) —
  `post_add` calls `snapshot_as_author()` for each added account, `post_remove`
  and `post_clear` delete the matching `FrozenAuthor` rows;
- `remove_author_from_article` (`pre_delete` on `FrozenAuthor`) — deletes the
  `ArticleAuthorOrder` row and then calls
  `instance.article.authors.remove(instance.author)`.

The sync is therefore bidirectional for writes that go *through the M2M*, but
there is nothing in the opposite direction: creating a `FrozenAuthor` directly —
which is what `snapshot_as_author()` and the submission flow do for co-authors —
never adds anything to `authors`.

The practical consequence is that **`frozenauthor__author` is a superset of
`authors` for ORM-mediated writes**. Switching the filter can therefore only
ever return *more* articles, never fewer. It also means the legacy-only state
(an `Account` in `authors` with no `FrozenAuthor`) cannot be constructed through
the related manager at all.

The qualifier matters: the signals fire on `Article.authors`'s related manager
and on `FrozenAuthor.delete()`, so the superset property breaks for writes that
go around them. Known break paths:

- direct writes to the through model (`Article.authors.through.objects.create()`
  / `bulk_create()`) — no `m2m_changed` is sent;
- `loaddata` / fixtures, raw SQL, and data migrations;
- clearing the link rather than the row — `FrozenAuthor.author = None` or
  `FrozenAuthor.objects.update(author=None)` fires no signal, leaving the M2M
  row orphaned.

None of these occur in `wjs/` today, so the conclusion holds for this codebase;
the pre-merge audit below is what confirms it for the actual data.

The rest of the codebase has already migrated: roughly twenty call sites in
`wjs_review` (permissions, assignment, role cache, forms, synctex, visibility)
use `author_accounts`. `wjs/jcom_profile/views.py` is the user-facing holdout.

### Corroboration from the existing test

`wjs/jcom_profile/tests/test_filter_articles_by_author` already computes its
*expected* result set as:

```python
articles_per_author = published_articles.filter(frozenauthor__author__in=[author], journal=journal)
```

and asserts on `article.frozenauthor_set.values_list("author_id", flat=True)`.
The test therefore already encodes frozen authors as the correct semantics; it
passes only because the test factory keeps both representations in sync. There
is no such guarantee in production data, which is why the bug is invisible to
the suite.

## Method

Filter the click-through page on the same relation `author_accounts` uses. The
reverse of `Account.objects.filter(frozenauthor__article=self)` is
`Article.objects.filter(frozenauthor__author=<account_pk>)`.

In `PublishedArticlesListView._get_filters()`:

```python
# was
"authors": self.kwargs.get("author", None),
# becomes
"frozenauthor__author": self.kwargs.get("author", None),
```

with `.distinct()` applied to the queryset, since joining across
`frozenauthor` can yield one row per matching `FrozenAuthor`.

Nothing else changes. `get_filter_by_configuration()` keeps resolving the URL
kwarg with `get_object_or_404(Account, pk=...)` — the URL still carries an
`Account` pk, so inbound links and `experimental_views.py`'s
`reverse("articles_by_author", kwargs={"author": self.author.id})` are
unaffected.

## Decisions

### 1. Switch to frozen authors rather than union both fields

**Chosen:** filter on `frozenauthor__author` alone.

The alternative considered was `Q(frozenauthor__author=pk) | Q(authors=pk)` as
a transition hedge, which cannot lose any article that the page returns today.
It was rejected because it re-introduces a false positive: since submission
puts the *submitting* user into `Article.authors`, an EO who submitted on an
author's behalf would have those articles listed on their own author page. It
also keeps a deprecated field in a user-facing query, deferring the same fix.

The residual risk of the chosen option — a published article holding an
`Account` in `authors` with no corresponding `FrozenAuthor` row — is much
smaller than it first appeared. As established above, the signals make that
state unreachable through the ORM, so it can only exist as historical residue
predating them or as the product of signal-bypassing writes. It is handled by
the audit in *Validation* below rather than by widening the query.

### 2. Do not backfill the `authors` M2M

Repairing the data instead of the query was rejected. The field is deprecated
and nothing maintains it going forward, so the drift — and the bug — would
return with the next published article.

### 3. Do not fix the sibling occurrences in this change

Three other live queries use the same deprecated field:

- `wjs/plugins/wjs_review/communication_utils.py:126` —
  `Article.objects.filter(authors=user)`
- `wjs/plugins/wjs_review/views.py:719` and `:743` —
  `.exclude(article__authors=self.request.user)`

Same bug class, different surfaces, each with its own correctness question (the
`exclude()` cases fail *open*, showing an author their own article, which is a
different severity from the reported symptom). They are out of scope for a 2h
issue and are recorded as a follow-up.

## Implementation

Test-driven, in this order:

1. **Failing test.** Extend `wjs/jcom_profile/tests/test_views.py` with a case
   that reproduces production drift: a published article whose `FrozenAuthor`
   links an `Account` that is *not* in `Article.authors`. Assert it appears on
   `/articles/author/<pk>/`. This fails against the current implementation.
2. **Fix.** Apply the `_get_filters()` change and `.distinct()`.
3. **Guard the inverse.** Add a case for an `Account` present in
   `Article.authors` but with no `FrozenAuthor` row — asserting it is *not*
   listed — so the intended semantics are pinned rather than incidental. Because
   the signals above make that state unreachable via `authors.add()`, the test
   writes the through-table row directly
   (`Article.authors.through.objects.create(...)`), which is the only way to
   reproduce the pre-signal shape.
4. **Regression.** The existing `test_filter_articles_by_author` and
   `test_filter_articles_by_author_not_found_error` must still pass unchanged.

## Validation

- Full `wjs/jcom_profile/tests/test_views.py` green, plus the wider
  `jcom_profile` suite for the shared `PublishedArticlesListView` paths
  (filter-by-section and filter-by-keyword share `_get_filters()`).
- `pre-commit run --all-files`.
- **Data audit before merge.** On a production-like database, confirm the
  chosen semantics lose nothing. The check has to be made **per (article,
  account) pair**, which means querying the through table rather than `Article`:

  ```python
  from django.db.models import Exists, OuterRef
  from submission.models import STAGE_PUBLISHED, Article, FrozenAuthor

  Article.authors.through.objects.filter(
      article__stage=STAGE_PUBLISHED,
  ).exclude(
      Exists(
          FrozenAuthor.objects.filter(
              article_id=OuterRef("article_id"),
              author_id=OuterRef("account_id"),
          ),
      ),
  ).values_list("article_id", "account_id")
  ```

  Each row returned is an `Account` in the legacy M2M with no frozen
  counterpart on that article, which would drop off that author's page.
  Expected result is empty — the signals make this state unreachable for
  anything written through the ORM, so a non-empty result means pre-signal
  residue or a signal-bypassing write path, and the finding comes back for a
  decision before merge.

  > **Do not** phrase this as
  > `Article.objects.filter(...).exclude(frozenauthor__author__in=F("authors"))`.
  > That was the first version of this audit and it under-reports: `exclude()`
  > across a multi-valued relation builds a subquery whose join is *not*
  > correlated with the outer author row, so it means "exclude any article where
  > *some* frozen author matches *some* legacy author". An article with authors
  > A and B where only A is frozen — the exact shape this audit exists to find —
  > is silently reported clean. Verified on the compiled SQL.
- Manual check against the reported case: author Rademann, JCOM article
  published 2026-08-17.

## Follow-ups (not done here)

- Migrate the remaining `Article.authors` sites in `wjs_review` to
  `author_accounts` / `frozenauthor__author`. Separate work item. Note the two
  `.exclude(article__authors=...)` cases fail *open* (they show an author their
  own article) whereas switching them would *hide* rows from a dashboard — a
  different risk profile that deserves its own review:
  - `communication_utils.py:126` — `Article.objects.filter(authors=user)`
  - `views.py:719` and `views.py:743` — `.exclude(article__authors=self.request.user)`
  - `signals.py:121` — `@receiver(m2m_changed, sender=Article.authors.through)`
    for role-cache invalidation. Since the review workflow no longer writes that
    M2M, this receiver is effectively dead for workflow articles, which may mean
    stale role caches. Worth checking on its own merits.
  - `logic.py:4981` — `frozen_authors if frozen_authors.exists() else
    article.authors.all()`, a benign fallback.
- Consider making the deprecation warning fail loudly in the test settings, so
  new uses of `Article.authors` are caught at development time. Note this is
  currently blocked by `pytest.ini`, which sets
  `filterwarnings = ignore::DeprecationWarning` — that is where the change would
  have to land.
- `get_queryset()`'s `# if text search is used, articles are already ordered`
  comment is already false: the `RawQuerySet` branch converts the relevance-ranked
  raw result into `filter(pk__in=[...])`, which discards that ordering. Pre-existing,
  untouched by this change, but worth fixing.
