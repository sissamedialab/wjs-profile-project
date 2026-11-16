# Director-authors must not see their own papers' editorial data — design

Work item: https://gitlab.sissamedialab.it/wjs/specs/-/work_items/3013 (`wjs/specs#3013`)
Repo: `wjs-profile-project`, plugin `wjs/plugins/wjs_review/`
Documentation language: English (confirmed).

## Intent

A journal director who is also an author of a paper must not see the editor's name, the reviewers,
the reminders or the editor-automatic-selection data of that paper. The article status page and the
Activity page already behave correctly. Several other director-facing pages still leak this data.
The goal is to treat a director-author as an author of that paper everywhere.

## Goals

- A director who is an author of a paper (as `FrozenAuthor` **or** as correspondence author):
  - does not find it in Pending, Archived, Production or "Search preprints", including when searching
    by title or ID;
  - gets 403 on that paper's reminders page, the reminders toggle and the editor automatic selection
    audit page, and does not see the audit link on the article page;
  - does not see it in the automatic selection audit list;
  - in Activity only sees messages where they are sender or recipient (already true for `FrozenAuthor`;
    must also hold for correspondence-author-only).
- A director who is not an author keeps full visibility everywhere, as today.
- The fix is structural: a later change to one director list must not be able to silently drop the exclusion
  (this is what happened to "Search preprints" after !1516).

## Non-goals

- Migrating every author lookup to `FrozenAuthor` (#3133).
- Improving the audit UI (#3266).
- Alerting EO when an author is a director/editor (#2765).
- Problem 4 of the issue (missing article type / submission date in lists): explicitly dropped from this
  work. For the record, local data (`janeway_prod`, JCOM article 4524) shows it is an `IncompleteSubmission`
  draft with no section and no `date_submitted`; no code change here.
- Any change for EO users who are also authors (see Decisions).

## Anti-goals

- Breaking EO access: EO users keep today's access, including EO users who are also authors.
- Hiding papers from directors who are *not* authors of them.

## Current state (verified in code, `wjs-develop` e9aaafaa)

- `DirectorPending` and `DirectorArchived` exclude `article__frozenauthor__author=request.user` (from !1516).
  `DirectorProduction` and `DirectorWorkOnAPaper` ("Search preprints") override `_apply_base_filters` and
  call `ArticleWorkflowBaseMixin._apply_base_filters` directly, so they apply no exclusion. "Search preprints"
  also renders editor (`article_current_editor`) and reviewer names.
- `ArticleReminders` (`has_admin_role or has_director_role`), `ToggleDisableReminders`,
  `AutomaticAssignmentAuditList`, `AutomaticAssignmentAuditArticle` (`has_eo_or_director_role`) use journal-level
  checks with no authorship test.
- `permissions.has_director_role_by_article` is already author-aware (`is_one_of_the_authors` covers
  correspondence author and all `author_accounts`).
  `permissions.has_eo_or_director_role_by_article` is not; its only caller is the
  `user_can_view_assignment_audit` template tag (`templatetags/wjs_permissions.py`).
- `communication_utils.get_messages_related_to_me` only covers `FrozenAuthor`, not correspondence author.
- `ArticleWorkflow.objects` is `ArticleWorkflowQuerySet.as_manager()`, so queryset methods chain.

## Design (decisions taken with the requester)

1. **Shared queryset predicate.** Add `ArticleWorkflowQuerySet.exclude_authored_by(user)` in `managers.py`:
   excludes workflows whose article has `frozenauthor__author=user` or `correspondence_author=user`.
2. **Director lists through one method (template method).** In `DirectorPending`:
   - `_apply_base_filters` = `ArticleWorkflowBaseMixin._apply_base_filters(self, qs)` (journal filter, called
     explicitly as today because of the MRO) `.exclude_authored_by(self.request.user)`, then
     `self._apply_state_filters(qs)`.
   - New hook `_apply_state_filters(qs)`: pending states in `DirectorPending`; archived states + the existing
     `sort_date` annotation in `DirectorArchived`; production states in `DirectorProduction`; returns `qs`
     unchanged in `DirectorWorkOnAPaper`.
   - The four classes no longer override `_apply_base_filters`. Existing state filters and annotations move
     as they are. Other `ArticleWorkflowBaseMixin` subclasses (EO, editor, author, reviewer) are untouched.
3. **Per-paper pages.**
   - `has_eo_or_director_role_by_article` becomes
     `has_eo_role(user) or has_director_role_by_article(instance, user)` (EO branch unchanged).
   - `ToggleDisableReminders` and `AutomaticAssignmentAuditArticle` use `has_eo_or_director_role_by_article`.
   - `ArticleReminders` uses `has_admin_role or has_director_role_by_article`.
   - `AutomaticAssignmentAuditList` applies `exclude_authored_by` to its queryset when the user has no EO role;
     EO users see everything.
4. **Activity messages.** `get_messages_related_to_me` gets the same two conditions as a `Q` on `Article`
   (it queries `Article`, not `ArticleWorkflow`, so no shared helper; the two predicates cross-reference
   each other in comments).

## Decisions made by the builder side (confirm or override)

- EO users who are also authors are unchanged (also for a user who is both EO and director: they keep access
  through the EO branch and are excluded only from the director lists).
- `DirectorWorkOnAPaper` keeps no state filter (search everything in the journal, minus own papers).

## Scope addition after the whole-branch review

The director "Pending Issues" list (`DirectorWorkOnIssue`, row template `lists/elements/issue/table_article.html`)
also showed the editor of a director's own paper. The requester decided to fix it in this MR: the paper stays
listed, and the editor cell is hidden when the viewer is a director (not EO) who is an author of the row's article
(`permissions.is_director_and_author`, template tag `user_is_director_author`). The row template is shared with the
EO and typesetter issue lists, which are unchanged.

## Clarifications from the builder check (verified in code)

- **Author sources are equivalent.** `Article.author_accounts` is derived from `FrozenAuthor`
  (`Account.objects.filter(frozenauthor__article=self)`), not from the legacy `authors` M2M. So
  `is_one_of_the_authors` (correspondence author + `author_accounts`) and `exclude_authored_by`
  (`frozenauthor__author` + `correspondence_author`) are the same predicate, and lists and per-paper pages agree.
  Tests assert both the `FrozenAuthor` and the correspondence-author-only cases.
- **Director* views** are reached only through the `director/...` URLs, whose `test_func` requires the director role;
  EO uses separate views. Nothing outside `views.py` and `urls.py` references the Director* classes or their
  `_apply_base_filters`; the only other `_apply_base_filters` overrides are in `views__production.py` (unrelated
  classes, untouched).

## Calls made by the builder side (confirm or override)

- **`ToggleDisableReminders`:** the object is a `Reminder` with a generic target. Resolve the workflow from the
  target (`target.article.articleworkflow` for review/editor assignments, the workflow itself if the target is one).
  If no workflow can be resolved, keep today's journal-level check, so no legitimate access regresses.
  The existing article-editor branch is untouched.
- **`ArticleReminders`:** the author check applies to both URLs (`wjs_article_reminders` and
  `wjs_reminders_per_assignment`). The per-assignment URL keeps its extra article-editor branch unchanged.
  `has_admin_role` stays unconditional (a journal admin who is also a director-author keeps access), consistent
  with the EO decision.
- **`AutomaticAssignmentAuditArticle`:** resolve the workflow from `article_pk` in `test_func`
  (404 if the article does not exist in the journal) and return 403 to a director-author even when the paper has
  no audit rows, i.e. the permission check runs before the `allow_empty=False` 404.

## Testing

- For each of Pending, Archived, Production, Search preprints: director who is `FrozenAuthor` does not see the
  paper; director who is only correspondence author does not see it; director who is not an author sees it.
- Search by title and by ID does not return the paper for a director-author.
- `get_messages_related_to_me` for a correspondence-author-only director.
- 403 for a director-author on reminders, reminders toggle, audit article page; paper absent from audit list;
  audit link hidden (`user_can_view_assignment_audit`).
- EO user (also author) keeps access; non-author director keeps access.
- Existing `tests/test_filterset.py` cases keep passing.

## Left to the builder

Test layout and fixture reuse, exact wording of docstrings, and any small refactor needed to keep
`_apply_state_filters` readable.

## Process notes (from the issue's Definition of Done)

MR references `main: #3013` with no closing keywords. The developer runs the functional test steps from the issue
(steps 1–7, minus the article type/date expectation of step 1) on a test environment and comments the result.
