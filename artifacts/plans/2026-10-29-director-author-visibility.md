# Director-author visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A journal director who is an author (frozen author or correspondence author) of a paper never sees that paper's editor, reviewers, reminders or automatic-selection data, on any director list or per-paper page.

**Architecture:** One shared queryset predicate (`ArticleWorkflowQuerySet.exclude_authored_by`) and one template method on `DirectorPending` (`_apply_base_filters` + `_apply_state_filters` hook) so no director list can drop the exclusion. Per-paper views switch to the author-aware `*_by_article` permission functions.

**Tech Stack:** Django, django-filter, pytest (`wjs/plugins/wjs_review/tests/`).

**Spec:** `artifacts/specs/2026-10-29-director-author-visibility-design.md` (issue `wjs/specs#3013`). Read it first; it fixes every decision below.

## Global Constraints

- Everything under `wjs/plugins/wjs_review/`; no other plugin changes. Docs, comments, commit messages in English.
- Author means `FrozenAuthor` OR correspondence author (`Article.author_accounts` is `FrozenAuthor`-derived, so `is_one_of_the_authors` and `exclude_authored_by` are the same predicate).
- EO behaviour is unchanged, including EO users who are also authors. `has_admin_role` stays unconditional in `ArticleReminders`.
- Only `ArticleWorkflowBaseMixin` subclasses `DirectorPending`, `DirectorArchived`, `DirectorProduction`, `DirectorWorkOnAPaper` change; they must not override `_apply_base_filters` any more.
- Problem 4 of the issue (missing article type/date) is out of scope: no code change.
- TDD: each task writes the failing test first. Run tests with `pytest wjs/plugins/wjs_review/tests/<file> -k <name>` from the repo root.

## Review Focus

- Director who is the correspondence author but has **no** `FrozenAuthor` row: must be excluded from all four lists and from Activity (Task 1, 2).
- Director who is a co-author (not correspondence): excluded everywhere (Task 2).
- Search by title (`abstract_or_title`) in "Search preprints" must not return the director's own paper (Task 2).
- A second, non-author director of the same journal still sees the paper in every list and page (Tasks 2, 3).
- Director-author asking for the audit page of a paper **with no audit rows** gets 403, not 404 (Task 3).
- Reminder whose target cannot be resolved to a workflow keeps today's journal-level access (Task 3).

---

### Task 1: Shared predicate and Activity messages

**Files:**
- Modify: `wjs/plugins/wjs_review/managers.py` (`ArticleWorkflowQuerySet`)
- Modify: `wjs/plugins/wjs_review/communication_utils.py:~150` (`get_messages_related_to_me`, director branch)
- Test: `wjs/plugins/wjs_review/tests/test_managers.py`, `wjs/plugins/wjs_review/tests/test_communications.py`

**Interfaces:**
- Produces: `ArticleWorkflowQuerySet.exclude_authored_by(self, user: Account) -> ArticleWorkflowQuerySet` excluding workflows whose article has `frozenauthor__author=user` or `correspondence_author=user`. Tasks 2 and 3 call it.

- [ ] **Step 1: Write failing tests.** In `test_managers.py`: `test_exclude_authored_by` using `assigned_article` and `director`; assert the workflow is returned before, and excluded after, for (a) `assigned_article.authors.add(director.janeway_account)`, (b) `assigned_article.correspondence_author = director; save()` with no frozen author row; assert it stays when the user is neither. In `test_communications.py`: `test_director_correspondence_author_does_not_see_own_article_messages` — create a message on the article (follow the existing message-creation pattern in that file) not involving the director; with the director as correspondence author only, `get_messages_related_to_me(director, article=...)` does not contain it; it does when the director is actor or recipient.
- [ ] **Step 2: Run them; expect FAIL** (`AttributeError: exclude_authored_by` / message still returned).
- [ ] **Step 3: Implement** `exclude_authored_by` in `managers.py` with `self.exclude(Q(article__frozenauthor__author=user) | Q(article__correspondence_author=user))`. In `communication_utils.py` replace the `authored_articles` query with `Article.objects.filter(Q(frozenauthor__author=user) | Q(correspondence_author=user)).values_list("pk", flat=True)`; keep the rest. Add a one-line comment in each place pointing at the other.
- [ ] **Step 4: Run both test files; expect PASS.**
- [ ] **Step 5: Commit** — `fix(wjs_review): treat correspondence author as author for director visibility`.

### Task 2: Director lists through one method

**Files:**
- Modify: `wjs/plugins/wjs_review/views.py:697-796` (`DirectorPending`, `DirectorArchived`, `DirectorProduction`, `DirectorWorkOnAPaper`)
- Test: create `wjs/plugins/wjs_review/tests/test_director_author_visibility.py`

**Interfaces:**
- Consumes: `ArticleWorkflowQuerySet.exclude_authored_by(user)` (Task 1).
- Produces: `DirectorPending._apply_state_filters(self, qs) -> QuerySet` hook; `DirectorPending._apply_base_filters` = `ArticleWorkflowBaseMixin._apply_base_filters(self, qs).exclude_authored_by(self.request.user)` then `self._apply_state_filters(qs)`.

- [ ] **Step 1: Write failing tests** in the new file. A helper `_director_list(view_class, user, fake_request, query="")` that mirrors `test_filterset.py` (`fake_request.GET = QueryDict(query)`, `fake_request.user = user`, `view.kwargs = {}`, `view.args = {}`, `view.request = fake_request`, `view.load_initial(fake_request)`, return `view.get_queryset()`). Parametrize over `(DirectorPending, DirectorArchived, DirectorProduction, DirectorWorkOnAPaper)`, putting the paper in a state that each list shows (use the `states_when_article_is_considered_*` constants the views import; "Search preprints" shows any state). Tests: `test_director_frozen_author_does_not_see_own_paper`, `test_director_correspondence_author_only_does_not_see_own_paper`, `test_non_author_director_sees_paper` (a second director via `create_jcom_user` + `add_account_role(DIRECTOR_ROLE, journal)`), and `test_search_by_title_does_not_return_own_paper` for `DirectorWorkOnAPaper` with `query="abstract_or_title=<title>"` (also asserts the non-author director finds it with the same query).
- [ ] **Step 2: Run; expect FAIL** for Production, WorkOnAPaper and the correspondence-only cases.
- [ ] **Step 3: Refactor the four classes** to the template method described in Interfaces: `DirectorPending._apply_state_filters` keeps the pending-states filter; `DirectorArchived` keeps archived states + the `sort_date` annotation; `DirectorProduction` the production states; `DirectorWorkOnAPaper` returns `qs`. Remove their `_apply_base_filters` overrides and update the docstrings (keep the note about calling the base mixin explicitly because of the MRO).
- [ ] **Step 4: Run the new file and `tests/test_filterset.py`; expect PASS.**
- [ ] **Step 5: Commit** — `fix(wjs_review): hide director-authored papers from all director lists`.

### Task 3: Per-paper pages and the audit list

**Files:**
- Modify: `wjs/plugins/wjs_review/permissions.py:294` (`has_eo_or_director_role_by_article`)
- Modify: `wjs/plugins/wjs_review/views.py` (`ArticleReminders.test_func` ~3290, `ToggleDisableReminders.test_func` ~3426, `AutomaticAssignmentAuditArticle.test_func` ~2321, `AutomaticAssignmentAuditList.get_queryset` ~2303)
- Test: `wjs/plugins/wjs_review/tests/test_permissions.py`, `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py`, `wjs/plugins/wjs_review/tests/test_director_author_visibility.py`

**Interfaces:**
- Consumes: `exclude_authored_by` (Task 1); existing `has_director_role_by_article(instance, user)`.
- Produces: `has_eo_or_director_role_by_article(instance, user) -> bool` = `has_eo_role(user=user) or has_director_role_by_article(instance, user)`.

- [ ] **Step 1: Write failing tests.**
  - `test_permissions.py`: `test_has_eo_or_director_role_by_article` parametrized `(director, is_author)` and EO user who is author → EO still True, director-author False, non-author director True.
  - `test_automatic_assignment_audit.py`: director-author gets 403 on `wjs-automatic-assignment-audit-article` both with an audit row and with none (the no-rows case must be 403, not 404); non-author director gets 200 with a row; paper absent from `response.context["audits"]` on `wjs-automatic-assignment-audit` for the director-author, present for non-author director and EO (EO who is author still sees it); `user_can_view_assignment_audit` template tag returns False for the director-author.
  - `test_director_author_visibility.py`: 403 for director-author on `wjs_article_reminders` and `wjs_reminders_per_assignment` (assignment created as in `assigned_article_with_reviewer`), 403 on `wjs_toggle_reminder` (create the `Reminder` through the normal flow as `test_reminders.py` does, e.g. assign a reviewer); 200/allowed for a non-author director; article editor of the assignment still allowed on both reminder URLs; a `Reminder` whose target has no resolvable workflow keeps journal-level director access on the toggle.
- [ ] **Step 2: Run; expect FAIL** (director-author currently gets 200).
- [ ] **Step 3: Implement.** Permission function as in Interfaces. `ArticleReminders.test_func`: replace `has_director_role(journal, user)` with `permissions.has_director_role_by_article(self.workflow, user)`, keep `has_admin_role` and the assignment-editor branch. `ToggleDisableReminders.test_func`: resolve the workflow from `self.object.target` (`target.article.articleworkflow`, or the target itself if it is an `ArticleWorkflow`); if resolved use `permissions.has_eo_or_director_role_by_article(workflow, user)`, else fall back to `base_permissions.has_eo_or_director_role(journal, user)`; keep the article-editor branch. `AutomaticAssignmentAuditArticle.test_func`: get the workflow with `get_object_or_404(ArticleWorkflow, article__pk=self.kwargs["article_pk"], article__journal=self.request.journal)` and use `has_eo_or_director_role_by_article`. `AutomaticAssignmentAuditList.get_queryset`: when `not base_permissions.has_eo_role(user)`, apply `.exclude(Q(article__frozenauthor__author=user) | Q(article__correspondence_author=user))` to the audit queryset (the queryset is on `AutomaticAssignmentAudit`, so use the article-prefixed `Q`, not `exclude_authored_by`).
- [ ] **Step 4: Run the three test files; expect PASS.**
- [ ] **Step 5: Commit** — `fix(wjs_review): hide reminders and editor audit from director-authors`.

### Task 4: Full verification

- [ ] **Step 1:** `pytest wjs/plugins/wjs_review/tests -x -q` (and `tests/test_filterset.py`, `test_visibility.py`, `test_reminders.py`); expect all PASS.
- [ ] **Step 2:** `pre-commit run --all-files`; expect clean.
- [ ] **Step 3:** Run the functional test steps 2–7 of issue #3013 locally where feasible (director A co-author, correspondence-only, non-author director B) and note the result for the issue comment.
