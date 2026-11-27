# "View only with Action Required" backend filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "View only with Action Required" a server-side filter over the whole paper list, default on the EO "Pending" list for an EO with a workload, with a `has_ac` annotation on every list query.

**Architecture:** One `Exists` expression (active AC visible to the user, `AttentionConditionQuerySet.active().visible_to()`) lives in `managers.py` and feeds two `ArticleWorkflowQuerySet` methods: one annotates `has_ac`, one filters on it. `BaseArticleWorkflowFilter` exposes the filter as the GET parameter `action_required`; `ArticleWorkflowBaseMixin.get_queryset` always annotates; `EOPending.load_initial` sets the default; the two-button group becomes GET submit buttons and `attention.html` skips rows without an AC.

**Tech Stack:** Django ORM (`Exists`, `OuterRef`), django-filter, Django templates, pytest.

**Spec:** `artifacts/specs/2026-11-24-action-required-backend-filter-design.md` (issue wjs/specs#3482; its functional steps T-1 to T-8 are the acceptance reference).

## Global Constraints

- Run the tests from `janeway-5.2/src` with `/home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest` and `PYTHONPATH=<this worktree>`; the `janeway-5.2/src/plugins/wjs_review` symlink must point to this worktree's `wjs/plugins/wjs_review` while testing and be restored to `/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project/wjs/plugins/wjs_review` afterwards (the symlink is outside this worktree: ask for the user's go-ahead before changing it).
- `black` line length 119, double quotes, `isort`, docstrings in imperative mood ending with a full stop (public methods and functions need one); user-facing strings use `gettext_lazy as _`; local imports only to break a circular import.
- Filtering lives in queryset methods, never in views or templates (`.claude/rules/architecture-django.md`); the filter and the annotation share one `Exists` definition.
- The visibility rule is `AttentionCondition.objects.active().visible_to(user, journal)` unchanged; the row highlight (`article_requires_attention_tt`) stays as it is.
- No migrations, no new dependencies. Templates: 2-space indentation, `{% endblock name %}`, `{{ var }}` spacing, `djlint` clean.
- Parameter name `action_required`, values `1` (only with action required) and `0` (all); annotation name `has_ac`.
- A list page keeps the same number of queries whatever the number of rows or ACs.
- Tests follow `.claude/rules/tests.md`: files `test_<module>.py`, functions `test_<description>`, descriptive assertion messages (no bare `assert`), real database (never mock the ORM).
- Commits: Conventional Commits in English with the `Co-Authored-By` trailer; MR text uses `main: #3482`, no closing keywords.

## Review Focus

- An EO whose only match is a row of the EO system user (no row of their own) must match; one whose own row for that code is dismissed must not (the `visible_to` fallback).
- A paper whose only active AC is unread messages matches; one with only dismissed ACs does not.
- `action_required` with an unknown value (`2`, `abc`) must not crash or hide papers.
- An EO who sets "EO in charge" to All and clicks "View All" keeps "All": the explicit choice cancels both defaults.
- An anonymous request to a list is redirected to login, not a server error (the annotation needs a user).
- Pagination counts and pages are those of the filtered list (`distinct()` and the `Max` annotation in `get_queryset` must not change them).

---

### Task 1: Queryset methods and the shared `Exists`

**Files:**
- Modify: `wjs/plugins/wjs_review/managers.py` (`ArticleWorkflowQuerySet`, plus a module-level helper)
- Test: `wjs/plugins/wjs_review/tests/test_action_required_filter.py` (new)

**Interfaces:**
- Produces: `_active_ac_exists(user: Account, journal: "Journal") -> Exists` (module level, private);
  `ArticleWorkflowQuerySet.with_has_ac(self, user: Account, journal: "Journal") -> "ArticleWorkflowQuerySet"` (annotates the boolean `has_ac`);
  `ArticleWorkflowQuerySet.requiring_attention(self, user: Account, journal: "Journal") -> "ArticleWorkflowQuerySet"` (keeps the workflows with an active AC visible to the user).

- [ ] **Step 1: Write the failing tests** in the new file. Fixtures: `assigned_article`, `section_editor`, `eo_user`, `journal`; helpers `ac_service.upsert_ac(article, user, code, message)`, dismissal with `AttentionCondition.objects.filter(...).update(status=AttentionCondition.Status.DISMISSED)`, unread with code `ac_service.HAS_UNREAD_MESSAGE`. Tests, each asserting `requiring_attention` and `has_ac` agree:
  - `test_active_ac_matches_and_has_ac_is_true`: an active AC for the editor makes the article's workflow match, and `with_has_ac` gives `has_ac is True` for it and `False` for another workflow.
  - `test_dismissed_only_does_not_match`: all the editor's ACs dismissed, no match.
  - `test_unread_messages_only_matches`: only an active `HAS_UNREAD_MESSAGE` AC, match.
  - `test_ac_of_another_user_does_not_match`: the AC belongs to the editor, the user is another section editor, no match.
  - `test_eo_matches_ac_of_eo_system_user_without_own_row`: AC for `get_eo_user(journal)`, user is `eo_user`, match.
  - `test_eo_own_dismissed_row_hides_eo_system_user_row`: same code, `eo_user` has a dismissed row and the EO system user an active one, no match.
  - `test_resolved_expired_suppressed_do_not_match`: parametrized over those three statuses.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest wjs/plugins/wjs_review/tests/test_action_required_filter.py -v` (command from Global Constraints)
Expected: FAIL with `AttributeError: ... has no attribute 'with_has_ac'` / `requiring_attention`.

- [ ] **Step 3: Implement** the three signatures above in `managers.py`. `_active_ac_exists` returns `Exists(AttentionCondition.objects.active().visible_to(user, journal).filter(article_id=OuterRef("article_id")))` (import `AttentionCondition` locally from `.models`, as the other methods in the module do). `with_has_ac` is `self.annotate(has_ac=_active_ac_exists(user, journal))`; `requiring_attention` is `self.filter(_active_ac_exists(user, journal))`. Check the SQL: `visible_to` has its own `OuterRef("article_id")` inside, nested in this `Exists`.

- [ ] **Step 4: Run to verify it passes**

Run: same command. Expected: PASS (all tests above).

- [ ] **Step 5: Lint and commit** (commit only if per-change commits were chosen at flow step 4)

```bash
pre-commit run --files wjs/plugins/wjs_review/managers.py wjs/plugins/wjs_review/tests/test_action_required_filter.py
git add wjs/plugins/wjs_review/managers.py wjs/plugins/wjs_review/tests/test_action_required_filter.py
git commit -m "feat(wjs_review): add has_ac annotation and requiring_attention filter to ArticleWorkflowQuerySet"
```

### Task 2: The `action_required` filter

**Files:**
- Modify: `wjs/plugins/wjs_review/filters.py` (`BaseArticleWorkflowFilter`)
- Test: `wjs/plugins/wjs_review/tests/test_action_required_filter.py`

**Interfaces:**
- Consumes: `ArticleWorkflowQuerySet.requiring_attention(user, journal)` (Task 1).
- Produces: `BaseArticleWorkflowFilter.action_required` (`django_filters.ChoiceFilter`, choices `("1", _("View only with Action Required"))` and `("0", _("View All"))`, `method="filter_action_required"`); `BaseArticleWorkflowFilter.filter_action_required(self, queryset: QuerySet, name: str, value: str) -> QuerySet`. It is inherited by the author, reviewer, staff, EO and work-on filtersets.

- [ ] **Step 1: Write the failing tests** (build the filterset as `test_filterset.py` does: `FilterClass(data={...}, queryset=ArticleWorkflow.objects.all(), journal=journal, request=fake_request)` with `fake_request.user` set):
  - `test_filter_one_keeps_only_matching_papers`: `{"action_required": "1"}` gives only the workflow with an active AC for the user.
  - `test_filter_zero_and_absent_keep_all`: `{"action_required": "0"}` and `{}` give every workflow.
  - `test_unknown_value_is_invalid_and_keeps_all`: `{"action_required": "2"}` and `"abc"`: `filterset.is_valid()` is False and the view-level behaviour (no filtering) is unchanged, no exception.
  - `test_every_filterset_has_the_filter`: parametrized over `AuthorArticleWorkflowFilter`, `ReviewerArticleWorkflowFilter`, `StaffArticleWorkflowFilter`, `EOArticleWorkflowFilter`, `WorkOnAPaperArticleWorkflowFilter`: `"action_required" in FilterClass.base_filters`.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest wjs/plugins/wjs_review/tests/test_action_required_filter.py -k "filter_one or filter_zero or unknown_value or every_filterset" -v`
Expected: FAIL (`KeyError` / filter missing).

- [ ] **Step 3: Implement** the filter and method on `BaseArticleWorkflowFilter` per the Interfaces: `"1"` returns `queryset.requiring_attention(self.request.user, self._journal)`, anything else returns `queryset` unchanged. Add `"action_required"` to nothing else (the form field is not rendered; the buttons carry the value).

- [ ] **Step 4: Run to verify it passes** (same command). Expected: PASS.

- [ ] **Step 5: Lint and commit** (if per-change commits)

```bash
pre-commit run --files wjs/plugins/wjs_review/filters.py wjs/plugins/wjs_review/tests/test_action_required_filter.py
git add wjs/plugins/wjs_review/filters.py wjs/plugins/wjs_review/tests/test_action_required_filter.py
git commit -m "feat(wjs_review): add the action_required backend filter to the paper lists"
```

### Task 3: List queryset annotation and the EO default

**Files:**
- Modify: `wjs/plugins/wjs_review/views.py` (`ArticleWorkflowBaseMixin.get_queryset`, `EOPending.load_initial`)
- Test: `wjs/plugins/wjs_review/tests/test_action_required_filter.py`

**Interfaces:**
- Consumes: `ArticleWorkflowQuerySet.with_has_ac(user, journal)` (Task 1), the `action_required` parameter (Task 2).
- Produces: every `ArticleWorkflowBaseMixin` list queryset carries `has_ac` for authenticated users; `EOPending.load_initial` rule: when `has_eo_with_workload_role(journal, user)` and `prefilter_by_eo` and neither a non-empty `search` nor an `action_required` key is in `request.GET`, it sets `eo_in_charge` to the user and `action_required` to `"1"`. An `action_required` key (even `"0"`) means the user chose, so neither default is applied.

- [ ] **Step 1: Write the failing tests** (view tests in the style of `test_views.py` around line 1971: `fake_request.GET = QueryDict(...)`, `view = EOPending()`, `view.request = fake_request`, `view.load_initial(fake_request)`, `view.get_queryset()`; users from `eo_user`, an EO with workload 0, `director`):
  - `test_list_queryset_rows_have_has_ac`: `get_queryset()` rows have `has_ac` True/False matching the ACs.
  - `test_eo_with_workload_gets_both_defaults`: empty GET gives `view.request.GET["action_required"] == "1"` and `"eo_in_charge"` set; the queryset holds only matching papers.
  - `test_explicit_choice_cancels_defaults`: with `?search=1`, and with `?action_required=0`, and with `?action_required=0&eo_in_charge=` neither default is added and every paper of the list is returned.
  - `test_eo_without_workload_gets_no_default`: workload 0, empty GET, no `action_required`/`eo_in_charge` added.
  - `test_other_eo_lists_have_no_default`: parametrized over `EOArchived`, `EOProduction`, `EOWorkOnAPaper`.
  - `test_filtered_page_count_is_the_filtered_list`: `Paginator(view.get_queryset(), 20).count` equals the number of matching workflows with `action_required=1`, with several versions/revisions present (guards `distinct()` and the `Max` annotation).
  - `test_anonymous_list_redirects_to_login`: `client.get(reverse("wjs_review_eo_pending"))` unauthenticated returns a redirect, not a 500.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest wjs/plugins/wjs_review/tests/test_action_required_filter.py -k "has_ac or defaults or default or explicit or page_count or anonymous" -v`
Expected: FAIL (no `has_ac`, defaults not set).

- [ ] **Step 3: Implement** in `ArticleWorkflowBaseMixin.get_queryset` the call `base_qs.with_has_ac(self.request.user, self.request.journal)` only when `self.request.user.is_authenticated`, before the filterset is applied; and in `EOPending.load_initial` the rule from Interfaces (keep the existing `prefilter_by_eo` and `has_eo_with_workload_role` checks, extend the "not chosen" condition, and set `data["action_required"] = "1"` beside `data["eo_in_charge"]`). Confirm `_apply_base_filters` of the other views still returns an `ArticleWorkflowQuerySet`.

- [ ] **Step 4: Run to verify it passes** (same command, then `pytest wjs/plugins/wjs_review/tests/test_filterset.py wjs/plugins/wjs_review/tests/test_views.py -k "pending or archived or EOPending" -v` to check no regression). Expected: PASS.

- [ ] **Step 5: Lint and commit** (if per-change commits)

```bash
pre-commit run --files wjs/plugins/wjs_review/views.py wjs/plugins/wjs_review/tests/test_action_required_filter.py
git add wjs/plugins/wjs_review/views.py wjs/plugins/wjs_review/tests/test_action_required_filter.py
git commit -m "feat(wjs_review): annotate list rows with has_ac and default the filter for EO with workload"
```

### Task 4: Buttons, `attention.html` and the page behaviour

**Files:**
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/lists/elements/filters_visibility.html` (rewrite), `wjs/plugins/wjs_review/templates/wjs_review/lists/elements/attention.html`
- Test: `wjs/plugins/wjs_review/tests/test_action_required_filter.py`

**Interfaces:**
- Consumes: the `action_required` GET parameter (Task 2) as `request.GET.action_required`, and `workflow.has_ac` (Task 3).
- Produces: in `filters_visibility.html`, two submit buttons inside the existing GET form: ids `selectAll` (`name="action_required" value="0"`, label "View All") and `selectAction` (`name="action_required" value="1"`, label "View only with Action Required"); the selected one has class `btn-primary`, the other `btn-outline-primary`; the inline `<script>` is removed. In `attention.html`, the `article_attention_conditions` call and the icons are skipped when `workflow.has_ac is False` (rows without the annotation still render as before).

- [ ] **Step 1: Write the failing tests** (client with `force_login`, URL names from `urls.py`: `wjs_review_list`, `wjs_review_archived_papers`, `wjs_review_eo_pending`, `wjs_review_eo_archived`, `wjs_review_eo_production`, `wjs_review_eo_workon`, `wjs_review_director_pending`, `wjs_review_director_archived`, `wjs_review_director_production`, `wjs_review_director_workon`, `wjs_review_author_pending`, `wjs_review_author_archived`, `wjs_review_reviewer_pending`, `wjs_review_reviewer_archived`; typesetter lists have no filterset and get no buttons):
  - `test_every_filtered_list_has_the_two_buttons`: parametrized over the URL names with the matching role user: the page contains `name="action_required" value="0"` and `name="action_required" value="1"`, does not contain `(current page)` and has no `visually-hidden` script.
  - `test_selected_button_follows_the_parameter`: `?action_required=1` renders `selectAction` with `btn-primary` and `selectAll` with `btn-outline-primary`; without the parameter (non-EO) the reverse.
  - `test_filter_is_applied_by_the_server_on_the_whole_list`: with more papers than one page (`paginate_by` 20 on `AuthorPending`, or patch `paginate_by`), the matching paper that would be on page 2 is on page 1 with `?action_required=1`.
  - `test_querystring_keeps_the_choice_in_pagination`: `response.context["querystring"]["action_required"] == "1"`.
  - `test_rows_without_ac_have_no_icons_and_rows_with_ac_keep_them`: warning icon `aria-label="1 attention condition"` present only for the paper with an AC.
  - `test_list_page_query_count_does_not_grow_with_acs`: `CaptureQueriesContext` on the EO list with 1 and with 10 papers having an AC gives the same number of queries.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest wjs/plugins/wjs_review/tests/test_action_required_filter.py -k "two_buttons or selected_button or whole_list or querystring or icons or query_count" -v`
Expected: FAIL (old buttons, inline script).

- [ ] **Step 3: Implement** `filters_visibility.html` (reuse `bootstrap_button` as the other `filters_*.html` do for the Search button: `button_type="submit"` with `name` and `value`; keep ids and the `btn-group`/`aria-label` markup) and the `attention.html` guard. Leave `filters_base.html`, `filters_staff.html`, `filters_eo.html`, `filters_workon.html` including it as today.

- [ ] **Step 4: Run to verify it passes** (same command, then the whole new test file and `pytest wjs/plugins/wjs_review/tests/test_attentionconditions.py -k listing -v`). Expected: PASS.

- [ ] **Step 5: Lint and commit** (if per-change commits)

```bash
pre-commit run --files wjs/plugins/wjs_review/templates/wjs_review/lists/elements/filters_visibility.html wjs/plugins/wjs_review/templates/wjs_review/lists/elements/attention.html wjs/plugins/wjs_review/tests/test_action_required_filter.py
git add wjs/plugins/wjs_review/templates wjs/plugins/wjs_review/tests/test_action_required_filter.py
git commit -m "feat(wjs_review): replace the frontend action-required script with server-side buttons"
```

### Task 5: Verification on the real data and the issue's steps

**Files:**
- No code. Throwaway scripts stay in the scratchpad.

- [ ] **Step 1: Run the new tests and the related existing suites**

Run: the new test file, `test_filterset.py`, `test_attentionconditions.py` and `test_views.py` (command from Global Constraints).
Expected: all PASS.

- [ ] **Step 2: Compare the final queries with the measured baseline** by re-running `scratchpad/perf/measure.py` against `janeway_jcap` with the worktree code (the symlink must point here): the page keeps 5 queries and the timings stay within the measured B figures (spec, Approaches table).

- [ ] **Step 3: Run the issue's functional steps T-1 to T-8** on a test environment or the local `janeway_jcap` site, and note the result for the issue comment (the developer's Definition of Done item). Update the steps in the issue if the behaviour differs.
