## 2026-10-27 — Keep the search query in the affiliation search pagination
**What:** The "Add Affiliation" organization search (profile and submission author variants) now keeps `q`, `next` and `paginate_by` in its page links. A new `querystring_without_page` filter in `wjs_tags` feeds the shared pagination include.
**Why:** specs#3044: searching "CERN" and then going to page 2 reset the list. WJS overrides `admin/core/organization_search.html` and includes wjs-themes' `wjs/base/elements/pagination.html`, which builds links as `?page=N&{{ querystring.urlencode }}`. `querystring` normally comes from `PaginatedViewMixin`, but these pages are rendered by Janeway's own `OrganizationListView`s, which don't set it. So the links were `?page=2&`.
**Decisions:** We fixed it in the WJS template with a filter, not by adding `context["querystring"]` to Janeway's views. That way there is no Janeway diff, and one template change covers both views. `PaginatedViewMixin._clean_querystring` is not rewritten to use the filter, because a mixin importing from a templatetags module is the wrong direction for a 4-line helper.
**Agent usage:**

| Stage | Agent/skill | Tokens | Time |
|---|---|---|---|
| Review | superpowers:requesting-code-review (general-purpose reviewer) | ≈72k | ≈1.3m |

**Considered & dropped:**
- Switching to Janeway's `common/elements/pagination.html`: Foundation markup in a Bootstrap page.
- `request.GET` passed straight through: it keeps the old `page`, so links would carry two `page` values.

**Follow-ups:** The reviewer noticed that wjs-themes' `core/news/index.html` and `journal/issue_display.html` pass `page=` to the same include, which reads `page_obj`/`querystring`. They may have no pagination or the same query loss. This was not verified and is out of scope here.

Local test gotchas:
- `pytest-freezegun` fails to import on Python 3.13 (`distutils`), so run with `-p no:freezegun`.
- Janeway's own `manage.py test` hits the WJS GDPR middleware unless the user has `gdpr_checkbox`.

**Refs:** wjs/specs#3044; Eval: 80% — artifacts/evaluations/2026-10-27-org-search-pagination-query.md
