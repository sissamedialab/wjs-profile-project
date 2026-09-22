# Author Filter on Frozen Authors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/articles/author/<pk>/` list every published article an account is a frozen author of, instead of querying the deprecated and unmaintained `Article.authors` M2M.

**Architecture:** One filter key changes in `PublishedArticlesListView._get_filters()`, from `authors` to `frozenauthor__author`, with `.distinct()` on the resulting queryset. This aligns the click-through page with `Article.author_accounts` (`Account.objects.filter(frozenauthor__article=self)`), which is what the rest of the codebase and the advanced search already use. No model, migration, template or URL change.

**Tech Stack:** Django 5.2, Janeway 1.8, pytest + pytest-django, factory_boy / pytest_factoryboy.

**Spec:** [`docs/superpowers/specs/2026-09-17-author-filter-frozen-authors-design.md`](../specs/2026-09-17-author-filter-frozen-authors-design.md)

## Global Constraints

- `Article.authors` is deprecated in Janeway 1.8 and raises a `DeprecationWarning` on every access. Do not add new reads of it; do not remove existing ones outside `wjs/jcom_profile/views.py` — the three sibling occurrences in `wjs_review` are an explicit follow-up, not part of this change.
- The URL kwarg stays an `Account` pk. `get_filter_by_configuration()` must keep resolving it with `get_object_or_404(Account, pk=...)`.
- Scope is `wjs/jcom_profile/views.py` and `wjs/jcom_profile/tests/test_views.py` only. No model or migration changes.
- Branch: `bugfix/issue-3048-filter-author-page-on-frozen-authors`. Commit messages in English, Conventional Commits.

---

### Task 1: Filter the author landing page on frozen authors

**Files:**
- Modify: `wjs/jcom_profile/views.py:765` (the `authors` key in `_get_filters()`) and `wjs/jcom_profile/views.py:816-823` (the `articles.filter(**filters)` chain in `get_queryset()`)
- Test: `wjs/jcom_profile/tests/test_views.py`

**Interfaces:**
- Consumes: `PublishedArticlesListView`, routed as `articles_by_author` in `wjs/jcom_profile/urls.py`; the `published_articles`, `editor`, `admin`, `journal`, `sections`, `keywords`, `press` fixtures and the `account_factory` fixture (registered via `pytest_factoryboy.register(AccountFactory)`) from `wjs/jcom_profile/tests/conftest.py`.
- Produces: no new public names. The filter key `"frozenauthor__author"` replaces `"authors"` inside `_get_filters()`, which stays a private method returning a `dict` of ORM lookups.

**Why the existing suite does not catch this:** `_create_published_articles()` in `conftest.py` calls `article.authors.add(owner)` and then `article.snapshot_authors()`, which builds the `FrozenAuthor` rows *from* `authors`. Both representations are therefore always identical in fixtures. The new test has to construct the drift explicitly, the way the review workflow produces it in production: a co-author who has a `FrozenAuthor` row but was never added to the M2M.

- [ ] **Step 1: Write the failing test**

Add to `wjs/jcom_profile/tests/test_views.py`, after `test_filter_articles_by_author`:

```python
@pytest.mark.django_db
def test_filter_articles_by_author_finds_frozen_only_author(
    editor, published_articles, press, admin, sections, keywords, journal, account_factory
):
    """An account linked only via FrozenAuthor is listed on its author page.

    This is the production shape reported in specs#3048: articles published
    through the review workflow get FrozenAuthor rows for their co-authors,
    but those co-authors are never added to the deprecated Article.authors
    M2M, which only ever receives the submitting user.
    """
    article = published_articles.filter(journal=journal).first()
    coauthor = account_factory()
    # Creates a FrozenAuthor with author=coauthor WITHOUT touching article.authors.
    coauthor.snapshot_as_author(article)

    assert coauthor not in article.authors.all()
    assert article in submission_models.Article.objects.filter(frozenauthor__author=coauthor)

    client = Client()
    url = reverse("articles_by_author", kwargs={"author": coauthor.pk})
    response = client.get(url)

    assert response.status_code == 200
    assert article in response.context["page_obj"].object_list
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest wjs/jcom_profile/tests/test_views.py::test_filter_articles_by_author_finds_frozen_only_author -v`

Expected: FAIL on the final assertion — the article is absent from `object_list`, because the view filters `authors=<pk>` and the co-author is not in that M2M. The two intermediate assertions must PASS; if either fails, the fixture is not producing the drift and the test is not reproducing the bug — stop and re-check `snapshot_as_author` before changing any implementation code.

- [ ] **Step 3: Change the filter key**

In `wjs/jcom_profile/views.py`, inside `_get_filters()`, replace:

```python
            "authors": self.kwargs.get("author", None),
```

with:

```python
            # specs#3048: Article.authors is deprecated in Janeway 1.8 and is not
            # maintained by the review workflow (only the submitting user is ever
            # added). FrozenAuthor is the published-state source of truth, and is
            # what Article.author_accounts and the advanced search already use.
            "frozenauthor__author": self.kwargs.get("author", None),
```

- [ ] **Step 4: Add a conditional `.distinct()` to the queryset**

> **Amended after code review.** This step originally applied `.distinct()`
> unconditionally. The `get_queryset()` chain is shared by five routes
> (`urls.py:26,91,97,102,107`), including `/articles/` — the journal's full
> published listing. `SELECT DISTINCT` over ~70 Article columns (including the
> eight translated `title_*` and eight `abstract_*` fields) forces Postgres to
> hash or sort the whole wide result set before `LIMIT`, and turns the
> paginator's `.count()` into `COUNT(*)` over a subquery. Only the multi-valued
> lookups can actually duplicate, so the dedup is now applied only when one of
> them is present.

Add the class attribute alongside `filter_by`:

```python
    #: Filter keys from :py:meth:`_get_filters` that span a multi-valued relation and can
    #: therefore yield duplicate rows. Their presence is what makes `.distinct()` necessary.
    MULTI_VALUED_FILTERS = frozenset({"frozenauthor__author", "keywords__pk", "keywords__in"})
```

and in `get_queryset()`:

```python
        articles = (
            articles.filter(**filters)
            .prefetch_related(
                "frozenauthor_set",
            )
            .exclude(
                pk__in=pinned_article_pks,
            )
        )
        if self.MULTI_VALUED_FILTERS & filters.keys():
            articles = articles.distinct()
```

Joining across `frozenauthor` can return one row per matching `FrozenAuthor`, so the deduplication is required rather than defensive — Janeway itself acknowledges the duplicate state in `remove_author_from_article`, which catches `ArticleAuthorOrder.MultipleObjectsReturned` with the comment *"the same account could be linked to the paper twice if the account is linked to multiple FrozenAuthor records"*. The same applies to `keywords__in` (multi-keyword advanced search), which could already return an article once per matching keyword — a pre-existing duplicate bug this fixes as a side effect.

- [ ] **Step 5: Run the new test to verify it passes**

Run: `pytest wjs/jcom_profile/tests/test_views.py::test_filter_articles_by_author_finds_frozen_only_author -v`

Expected: PASS

- [ ] **Step 6: Pin the inverse semantics**

Add the complementary test, so the switch is asserted rather than incidental — an account in the legacy M2M with no `FrozenAuthor` row must *not* appear:

```python
@pytest.mark.django_db
def test_filter_articles_by_author_ignores_legacy_only_author(
    editor, published_articles, press, admin, sections, keywords, journal, account_factory
):
    """An account present only in the deprecated M2M is not listed.

    Submission adds the submitting user to Article.authors, which for an
    EO-assisted submission is not an author at all. Frozen authors are the
    only source of truth for the public author page.
    """
    article = published_articles.filter(journal=journal).first()
    non_author = account_factory()
    article.authors.add(non_author)

    assert not article.frozenauthor_set.filter(author=non_author).exists()

    client = Client()
    url = reverse("articles_by_author", kwargs={"author": non_author.pk})
    response = client.get(url)

    assert response.status_code == 200
    assert article not in response.context["page_obj"].object_list
```

- [ ] **Step 6b: Pin the deduplication (added after code review)**

`.distinct()` was not covered by any test — it could be deleted and the module would still pass. Add a case that creates a second `FrozenAuthor` for the same `(article, account)` directly, since `snapshot_as_author()` uses `get_or_create` and cannot produce it:

```python
@pytest.mark.django_db
def test_filter_articles_by_author_deduplicates_repeated_frozen_authors(
    editor, published_articles, press, admin, sections, keywords, journal, account_factory
):
    article = published_articles.filter(journal=journal).first()
    coauthor = account_factory()
    coauthor.snapshot_as_author(article)
    submission_models.FrozenAuthor.objects.create(
        article=article,
        author=coauthor,
        first_name=coauthor.first_name,
        last_name=coauthor.last_name,
        order=article.next_frozen_author_order(),
    )

    assert article.frozenauthor_set.filter(author=coauthor).count() == 2

    client = Client()
    url = reverse("articles_by_author", kwargs={"author": coauthor.pk})
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert list(response.context["page_obj"].object_list) == [article]
```

Verify it fails with the `if ...: articles.distinct()` block removed, then passes with it restored.

- [ ] **Step 7: Run the author-filter tests together**

Run: `pytest wjs/jcom_profile/tests/test_views.py -v -k "filter_articles_by_author"`

Expected: PASS — including the pre-existing `test_filter_articles_by_author` and `test_filter_articles_by_author_not_found_error`, neither of which may be modified. `test_filter_articles_by_author` already computes its expectation with `frozenauthor__author__in=[author]`, so it should now agree with the implementation for the right reason rather than by fixture coincidence.

- [ ] **Step 8: Run the sibling filter tests for `_get_filters()` regressions**

Run: `pytest wjs/jcom_profile/tests/test_views.py -v -k "filter_articles or search"`

Expected: PASS. `_get_filters()` and the `get_queryset()` chain are shared with filter-by-section, filter-by-keyword and the search form, so the added `.distinct()` has to be clean for those paths too.

- [ ] **Step 9: Run the full app test module**

Run: `pytest wjs/jcom_profile/tests/test_views.py`

Expected: PASS, no new failures against the branch point.

- [ ] **Step 10: Run pre-commit**

Run: `pre-commit run --all-files`

Expected: PASS (or only auto-fixes, which get staged).

- [ ] **Step 11: Commit**

Per the flow's step 4 decision on commit strategy. If committing as we go:

```bash
git add wjs/jcom_profile/views.py wjs/jcom_profile/tests/test_views.py
git commit -m "fix(views): filter author landing page on frozen authors

Article.authors is deprecated in Janeway 1.8 and is not maintained by the
review workflow, which only adds the submitting user. Filter on
frozenauthor__author instead, matching Article.author_accounts and the
advanced search.

Refs specs#3048"
```

---

## Validation

Beyond the per-step test runs:

- **Data audit before merge** (from the spec). On a production-like database, confirm the new semantics lose nothing. The check must be per (article, account) pair, so it queries the through table:

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

  Expected empty. Each row returned is an `Account` in the legacy M2M with no frozen counterpart on that article, which would drop off that author's page — bring the finding back for a decision rather than merging. See the spec for why the `exclude(frozenauthor__author__in=F("authors"))` phrasing must not be used: it is uncorrelated and under-reports.

- **Manual check** against the reported case: author Rademann, JCOM article published 2026-08-17, on a environment with production data.

## Out of scope

Tracked in the spec's follow-ups, not to be touched here:

- `wjs/plugins/wjs_review/communication_utils.py:126` — `Article.objects.filter(authors=user)`
- `wjs/plugins/wjs_review/views.py:719` and `:743` — `.exclude(article__authors=self.request.user)`
