# Design: restrict anonymous access on `wjs/jcom_profile/urls.py`

**Status:** Post-factum — written after implementation and MR !1479. Recorded
here because the work was done interactively (audit → fix, one view group at
a time) rather than from a prior spec, and the reasoning behind what was
protected, what was deliberately left open, and what was scoped out is worth
keeping.

## Context

Related to issue #2062 (disabling the public front-end when
`frontend_enabled` is `False`, addressed on the `janeway` side by
`feat: disable public pages redirect if frontend_enabled is False`). While
that change was in flight, a companion question came up: of all the views
`wjs/jcom_profile/urls.py` routes to, which ones are reachable by an
unauthenticated user, and is that intentional in each case?

There was no existing inventory of this — auth exposure was implicit in each
view's base class / decorator, not documented anywhere. This spec is that
inventory, plus the fixes applied as a result.

## Method

Read every view reachable from `wjs/jcom_profile/urls.py` (`views.py`,
`profile/views.py`, `newsletter/views.py`, `experimental_views.py`,
`drupal_redirect_views.py`, plus the two Janeway-core views this app
overrides — `journal.views.article` and `journal.views.serve_article_pdf`)
and checked each for `LoginRequiredMixin` / `UserPassesTestMixin` /
`login_required` / `user_passes_test`, and, separately, for
`journal_decorators.frontend_enabled` (a distinct concern from auth: whether
the view respects a journal's "front end disabled" flag at all).

Findings were narrowed down across the conversation by explicit exclusion,
each for a documented reason rather than by re-auditing:

1. Profile edit views, `assignment_parameters`, `edit_newsletters`,
   `author_search`, `user_autocomplete`, `set_notify_hijack` — already
   guarded (`LoginRequiredMixin` or `user_passes_test`). Not touched.
2. Newsletter views (`register_newsletters`, `unsubscribe_newsletter`, etc.)
   — anonymous by design; the class names say so
   (`AnonymousUserNewsletterRegistration`, ...) and the flows are
   token/email-based, which is the correct pattern for a user who by
   definition doesn't have an account yet. Excluded from the fix list.
3. `keywords_list` — public keyword listing, equivalent in exposure to the
   article listings that were kept public (§Decisions). Excluded.
4. Drupal-era redirect views (`jcom_redirect_*`, `drupal_*_redirect`) —
   pure 301/302 redirects to public content, no data exposed beyond what a
   URL guess already reveals. Excluded.
5. `journal_issues` (`views.issues`) and `article_view`
   (`journal_views.article`) — already gated by
   `@journal_decorators.frontend_enabled` (and `has_journal`). Excluded once
   that was confirmed; frontend-enabled gating is treated as an accepted
   substitute for a login requirement on genuinely public content.

That left three groups to actually fix.

## Decisions

### 1. IMU (Insert Many Users) admin flow → `LoginRequiredMixin`

`IMUStep1`/`IMUStep2`/`IMUStep3` (`si-imu-1/2/3`) drive bulk account
creation/import for a special issue. `IMUStep1` had no guard at all;
`IMUStep2` and `IMUStep3` were explicitly marked `# TODO: protect me!` in
source — an acknowledged, not accidental, gap.

Fix: add `LoginRequiredMixin` to all three.

**Known limitation, deliberately left open:** `LoginRequiredMixin` only
requires *an* authenticated account, not staff/admin. Given this is a
bulk-user-import admin tool, the more correct guard is a staff/permission
check — e.g. `UserPassesTestMixin` on `is_staff`, matching the existing
`StaffWorkloadParametersUpdate` pattern in the same file. This was flagged
in the MR description as explicit follow-up rather than folded into this
change, to keep the fix minimal and reviewable against the specific gap
that was found (fully anonymous, not "any logged-in user").

### 2. Experimental force-graph views → `LoginRequiredMixin`

`IssuesForceGraph`, `AuthorsForceGraph`, `AuthorsKeywordsForceGraph`,
`ArticlesByKeywordForceGraph` (the `experimental/` "Easter-egg" URLs) had no
guard. `IssuesForceGraph` even carried a comment recording that the author
wanted to apply `has_journal`/`frontend_enabled` to it but didn't know how
to do so on a class-based view (`# TODO: how do I apply Janeway's function
decorators to class-based views?`).

Fix: add `LoginRequiredMixin` to all four; the stale TODO on
`IssuesForceGraph` was removed since it specifically asked how to gate
class-based views, and the answer (`LoginRequiredMixin`, or
`@method_decorator(..., name="dispatch")` per below) is now demonstrated in
this same file/PR.

**Scoped out:** `has_journal`/`frontend_enabled` parity for these four
views was not applied — only the login requirement, which was the specific
ask. Noted here so it isn't mistaken for an oversight.

### 3. `PublishedArticlesListView` → `frontend_enabled` decorator (not login)

This view backs `search`, `journal_articles`, `articles_by_keyword`,
`articles_by_section`, and `articles_by_author` — all genuinely public
content (published articles). Requiring login here would be a regression,
not a fix. The actual gap was narrower: this override didn't respect a
journal's `disable_front_end` flag the way Janeway core's own
`PublishedArticlesListView` (`journal/views.py`) does.

Fix:
```python
@method_decorator(journal_decorators.frontend_enabled, name="dispatch")
class PublishedArticlesListView(PaginatedViewMixin, FormMixin, ListView):
    ...
```
mirroring core's `@method_decorator(has_journal, name="dispatch")` +
`@method_decorator(decorators.frontend_enabled, name="dispatch")` stack on
its `PublishedArticlesListView` (`FacetedArticlesListView` subclass).

`has_journal` was deliberately **not** added, even though core applies it:
`frontend_enabled`'s implementation (`journal/decorators.py`) already
guards with `if request.journal and request.journal.disable_front_end:`,
so it's safe when `request.journal` is falsy — `has_journal` would be an
independent, unrequested behavior change (i.e. it would start rejecting
requests with no journal in context, which this view didn't do before).

### Left as intentionally public

- `confirm_gdpr_acceptance` (`accept_gdpr`) — token-based GDPR confirmation
  for invited users who don't have a session yet. Explicitly confirmed
  correct as-is.
- `journal_views.serve_article_pdf` — public PDF download for published
  articles; no `frontend_enabled` guard in Janeway core either, so parity
  was preserved rather than introduced.

## Implementation

Single commit, single MR:

- `wjs/jcom_profile/views.py`: `LoginRequiredMixin` added to `IMUStep1`,
  `IMUStep2`, `IMUStep3` (dropping their `# TODO: protect me!` comments);
  `@method_decorator(journal_decorators.frontend_enabled, name="dispatch")`
  added to `PublishedArticlesListView`; `method_decorator` import added.
- `wjs/jcom_profile/experimental_views.py`: `LoginRequiredMixin` added to
  `IssuesForceGraph`, `AuthorsForceGraph`, `AuthorsKeywordsForceGraph`,
  `ArticlesByKeywordForceGraph` (dropping the stale TODO on the first);
  `LoginRequiredMixin` import added.

Branch `feature/issue-2062-protect-views` → MR !1479 → `wjs-develop`.
Pre-commit (isort/black/flake8/pyupgrade/django-upgrade/etc.) passed clean
on the commit.

## Validation

Pre-commit hooks passed on commit. No new automated test coverage was added
in this pass — see Follow-ups.

## Follow-ups (not done here)

1. Tighten `IMUStep1/2/3` from "any authenticated user" to a staff/role
   check, matching `StaffWorkloadParametersUpdate`'s `UserPassesTestMixin`
   pattern.
2. Add regression tests asserting anonymous requests to `si-imu-1/2/3` and
   the four `experimental/*` URLs now redirect to login (currently
   uncovered — their absence is arguably how the gap went unnoticed as long
   as it did).
3. Consider whether `experimental_views` should also gain
   `has_journal`/`frontend_enabled` parity, or whether "logged-in only" is
   an accepted permanent posture for Easter-egg views.
