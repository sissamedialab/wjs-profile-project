# AC Dismissal Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users dismiss and restore their own attention conditions (ACs), keep dismissals through every
re-evaluation, and give templates the data they need, without the final UI.

**Architecture:** A new `DISMISSED` status on `AttentionCondition`; `ac_service` keeps it on upsert and resolves
it like ACTIVE; `visible_to()` stops duplicating EO rows. A logic class + form + HTMX view toggle the status.
A read-path value object (`ArticleAttentionConditions`) feeds a template filter, the listing prefetch and a minimal panel.

**Tech Stack:** Django 5.2 (inside Janeway), django-fsm workflow, HTMX, pytest + pytest-django.

**Spec:** `artifacts/specs/2026-11-04-ac-dismissal-backend-design.md`

## Global Constraints

- Keep one reused row per `(article, user, code)`; never delete AC rows.
- `HAS_UNREAD_MESSAGE` (`ac_service.HAS_UNREAD_MESSAGE == "has_unread_message"`) is never dismissible.
- A dismissal is per user and per article: only `ac.user == request.user` can toggle.
- Toggle only ACTIVE ↔ DISMISSED; RESOLVED (and EXPIRED/SUPPRESSED) cannot be toggled.
- "New AC" (AC-4) = the code stopped applying (row RESOLVED) and applied again; no per-trigger fingerprint.
- The legacy `article_requires_attention_tt` filter, `_prefetched_acs` dict and current templates keep working.
- The view never returns an empty response; the template decides what is shown.
- Logic classes: `@dataclasses.dataclass`, `run()` in `transaction.atomic()`, `ValidationError` on unmet
  requirements; forms call the logic class in `save()`; views catch `ValidationError` → `form_invalid`.
- Filtering goes through `AttentionConditionQuerySet` methods, never raw `.objects.filter` in views/templatetags.
- New URL names hyphenated. Black 119, double quotes, docstrings on public functions, 2-space templates.
- Tests: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`, pytest, fixtures from `conftest.py`
  (`assigned_article`, `eo_user`, `normal_user`, `create_jcom_user`, `eo_group`, `journal`, `client`) and the
  file's helper `_create_eo_human`. Every assert carries a message.

**Running tests** (outside the repo boundary — needs the user's go-ahead once per flow), from
`/Users/sfedoruk/Projects/sissa/janeway/src`:

```bash
/Users/sfedoruk/.pyenv/versions/janeway/bin/python -m pytest \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py -k "<selector>" -p no:freezegun
```

If debug-toolbar's `MIGRATION_MODULES.setdefault` error appears, use the scratch-settings workaround from the
user's memory note; add `--create-db` once if the reused DB is stale. Never run the full suite per task.

## Review Focus

1. **EO member without an `AccountRole` on the journal** opens the listing: expects to still see the normal ACs
   (system user's rows, read-only), each once. → Task 3 test `test_eo_without_personal_row_sees_system_row_read_only`.
2. **Message read/unread toggle after a dismissal** (runs `evaluate_all()`): the dismissed AC must stay dismissed.
   → Task 2 test `test_dismissed_ac_survives_evaluate_all`.
3. **Nightly rebuild** with a dismissed AC whose condition still holds. → Task 2 test
   `test_dismissed_ac_survives_rebuild`.
4. **Toggling someone else's AC or an AC of another article via the URL** (tampered `ac_pk`/`pk`): 403/404, no
   change. → Task 6 test `test_toggle_view_rejects_foreign_or_mismatched_ac`.
5. **Listing with more than one page**: ACs for articles off the current page are not loaded, legacy display
   unchanged for the page. → Task 5 test `test_listing_prefetch_limited_to_current_page`.

---

### Task 1: DISMISSED status, migration, queryset helpers

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (`AttentionCondition.Status`, ~line 2654)
- Create: `wjs/plugins/wjs_review/migrations/0026_alter_attentioncondition_status.py`
- Modify: `wjs/plugins/wjs_review/managers.py` (`AttentionConditionQuerySet`, ~line 274)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Produces: `AttentionCondition.Status.DISMISSED == "dismissed"`;
  `AttentionConditionQuerySet.dismissed() -> AttentionConditionQuerySet`;
  `AttentionConditionQuerySet.displayable() -> AttentionConditionQuerySet` (ACTIVE or DISMISSED);
  `AttentionConditionQuerySet.for_articles(article_ids: Iterable[int]) -> AttentionConditionQuerySet`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_displayable_and_dismissed_querysets(assigned_article: Article):
    article = assigned_article
    editor = WjsEditorAssignment.objects.get_current(article).editor
    AttentionCondition.objects.all().delete()
    active = ac_service.upsert_ac(article, editor, ac_service.REVIEWER_LATE, "Reviewer is late")
    dismissed = ac_service.upsert_ac(article, editor, ac_service.NEEDS_ASSIGNMENT, "Needs assignment")
    AttentionCondition.objects.filter(pk=dismissed.pk).update(status=AttentionCondition.Status.DISMISSED)
    resolved = ac_service.upsert_ac(article, editor, ac_service.REVIEWS_COMPLETED, "Reviews completed")
    ac_service.resolve_ac(article, editor, ac_service.REVIEWS_COMPLETED)
    assert set(AttentionCondition.objects.dismissed()) == {dismissed}, "Only DISMISSED rows"
    assert set(AttentionCondition.objects.displayable()) == {active, dismissed}, "ACTIVE and DISMISSED rows"
    assert resolved not in AttentionCondition.objects.for_articles([article.pk + 1000]), "Filtered by article"
```

Extend `test_attention_conditions_page_groups_per_user_and_orders_per_status`: add a dismissed editor AC
(e.g. `EDITOR_REVIEW_OVERDUE`, priority 5) and assert the editor group is
`[more_urgent, less_urgent, dismissed_ac, resolved]` ("Dismissed ACs come after active and before resolved").

- [ ] **Step 2: Run the tests, expect FAIL** (`AttributeError: DISMISSED` / `dismissed`).

- [ ] **Step 3: Implement**
  - `Status.DISMISSED = "dismissed", _("Dismissed")`.
  - Hand-written migration `0026_alter_attentioncondition_status`, dependency
    `("wjs_review", "0025_editor_decline_reason_busy_editorial")`, one `AlterField` on `status` with the new choices
    (copy field kwargs from the model). Do not generate it in the docker env.
  - Queryset methods as in Interfaces; `by_user_and_relevance()` adds `When(status=DISMISSED, then=1)` and moves
    RESOLVED to 2, default 3.

- [ ] **Step 4: Run the tests, expect PASS** (both tests above).

- [ ] **Step 5: Commit** (if per-change commits were chosen) — `feat(wjs_review): add dismissed status to attention conditions`

---

### Task 2: Dismissal survives upsert; resolvers cover DISMISSED

**Files:**
- Modify: `wjs/plugins/wjs_review/ac_service.py` (`upsert_ac` ~236, `resolve_all_for_user_on_article` ~323,
  `resolve_for_role_batch` ~499, `resolve_ac_batch` ~522)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Consumes: `Status.DISMISSED`, `displayable()` (Task 1).
- Produces: `upsert_ac(...)` same signature; returned instance reflects the stored status.

- [ ] **Step 1: Write the failing tests**
  - `test_upsert_keeps_dismissed_and_refreshes_message`: upsert, set DISMISSED, upsert again with message "B" and
    priority 3 → `status == DISMISSED`, `message == "B"`, `priority == 3`, and the returned object has the same values.
  - `test_upsert_reactivates_resolved`: upsert, resolve, upsert → `status == ACTIVE` (AC-4).
  - `test_dismissed_resolved_then_retriggered_is_active`: upsert, dismiss, `resolve_ac`, upsert → ACTIVE.
  - `test_batch_resolvers_resolve_dismissed` (parametrize over the three functions:
    `resolve_all_for_user_on_article(article, user)`, `resolve_ac_batch(article, user, [code])`,
    `resolve_for_role_batch(article, constants.SECTION_EDITOR_ROLE, [code])` with the current editor) →
    a DISMISSED row becomes RESOLVED.
  - `test_dismissed_ac_survives_evaluate_all`: on `assigned_article` with an editor AC produced by
    `ACStateEvaluator(state=..., article=...).evaluate_all()` (pick a code the fixture state actually raises, e.g.
    by freezing time past the editor deadline as `test_editor_is_late` does), dismiss it, call `evaluate_all()`
    again → still DISMISSED.
  - `test_dismissed_ac_survives_rebuild`: same setup, then `attention_conditions_rebuild()` (helper from
    `test_helpers`) → still DISMISSED.

- [ ] **Step 2: Run, expect FAIL** (status reset to ACTIVE; batch resolvers leave DISMISSED).

- [ ] **Step 3: Implement**
  - `upsert_ac`: `update_or_create(..., defaults={message, priority, source}, create_defaults={..., "status": ACTIVE})`,
    then, if the row's status is neither ACTIVE nor DISMISSED, set it to ACTIVE (single `update` + set attribute).
    Update the docstring: DISMISSED is kept, other non-active statuses reactivate.
  - The three resolvers: `status__in=(ACTIVE, DISMISSED)` instead of `status=ACTIVE`; update their docstrings.

- [ ] **Step 4: Run, expect PASS**; also run the existing `-k "unread or postpone or appeal"` tests (regression).

- [ ] **Step 5: Commit** — `feat(wjs_review): keep dismissed attention conditions across re-evaluation`

---

### Task 3: EO visibility without duplicates, with fallback

**Files:**
- Modify: `wjs/plugins/wjs_review/managers.py` (`visible_to`, ~313)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Produces: `visible_to(user, obj)` same signature. Rule: own rows; for EO users with `obj` given, plus the EO
  system user's rows where `code == HAS_UNREAD_MESSAGE` **or** no own row with the same `(article, code)` exists
  among the user's `displayable()` rows (use `~Exists(...OuterRef("article_id"), OuterRef("code"))`).

- [ ] **Step 1: Write the failing tests**
  - `test_eo_member_sees_each_normal_ac_once`: `_create_eo_human`, `upsert_for_role(article, "eo", code, msg)` →
    `visible_to(eo_human, article).filter(code=code).count() == 1` and that row's `user == eo_human`.
  - `test_eo_member_still_sees_shared_unread_ac`: an active `HAS_UNREAD_MESSAGE` on `get_eo_user(article)` is in
    `visible_to(eo_human, article)`.
  - `test_eo_dismissal_is_personal`: two EO humans; dismiss EO1's row → EO2's visible row for the code is ACTIVE,
    EO1's visible row is DISMISSED and no system row for that code is visible to EO1.
  - `test_eo_without_personal_row_sees_system_row_read_only`: EO-group member with no `AccountRole` on the journal
    (`create_jcom_user` + `groups.add(eo_group)` only), `upsert_for_role("eo", ...)` → exactly one visible row for
    the code, owned by `get_eo_user(article)`.

- [ ] **Step 2: Run, expect FAIL** (count 2; no fallback distinction).

- [ ] **Step 3: Implement** the rule above; rewrite the docstring (why the fallback exists, link to #3272 note 81236).

- [ ] **Step 4: Run, expect PASS**, plus `-k "eo"` regression.

- [ ] **Step 5: Commit** — `fix(wjs_review): show each attention condition once to EO members`

---

### Task 4: Toggle logic class and form

**Files:**
- Modify: `wjs/plugins/wjs_review/logic.py` (new class near other small logic classes)
- Modify: `wjs/plugins/wjs_review/forms.py` (next to `ToggleMessageReadForm`, ~1212)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Consumes: `Status.DISMISSED` (Task 1).
- Produces:
  - `ToggleAttentionConditionDismissal(attention_condition: AttentionCondition, user: Account)` dataclass with
    `run() -> AttentionCondition` (re-reads with `select_for_update()` inside `transaction.atomic()`, checks owner,
    code ≠ `HAS_UNREAD_MESSAGE`, status in ACTIVE/DISMISSED, else `ValidationError`; flips and saves `status` only).
  - `ToggleAttentionConditionDismissalForm(forms.ModelForm)`, `Meta.fields = []`, constructor kwarg `user: Account`;
    `save()` follows the architecture-rule pattern (run logic, `add_error(None, e)` + re-raise on
    `ValidationError`, `refresh_from_db()`, return instance).

- [ ] **Step 1: Write the failing tests**
  - `test_toggle_dismissal_flips_active_and_dismissed`: run twice → DISMISSED then ACTIVE.
  - `test_toggle_dismissal_rejects` (parametrize): other user's AC; `HAS_UNREAD_MESSAGE`; RESOLVED AC →
    `pytest.raises(ValidationError)` and status unchanged.
  - `test_toggle_dismissal_form_adds_non_field_error`: form with an unread AC → `ValidationError` raised and
    `form.non_field_errors()` not empty.

- [ ] **Step 2: Run, expect FAIL** (ImportError).
- [ ] **Step 3: Implement** per Interfaces; error messages via `gettext_lazy`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(wjs_review): add attention condition dismissal toggle logic`

---

### Task 5: Read path (value object, template filter, listing and detail data)

**Files:**
- Modify: `wjs/plugins/wjs_review/ac_service.py` (new section "Read path")
- Modify: `wjs/plugins/wjs_review/templatetags/wjs_review.py` (new filter after `article_requires_attention_tt`)
- Modify: `wjs/plugins/wjs_review/views.py` (`ArticleWorkflowBaseMixin.get_context_data` ~358,
  `ArticleDetails.get_context_data`)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Consumes: `displayable()`, `for_articles()`, `visible_to()` (Tasks 1, 3).
- Produces:
  - `@dataclass ArticleAttentionConditions`: `active: list[AttentionCondition]`, `dismissed: list[AttentionCondition]`,
    `unread: Optional[AttentionCondition]`; properties `has_active -> bool` (any active or unread),
    `has_any -> bool`. Each AC in `active`/`dismissed` carries `dismissible: bool` (`ac.user_id == viewer.pk`).
    Lists ordered by `priority`, `created_at`. `unread` only if ACTIVE.
  - `ac_service.get_article_attention_conditions(article: Article, user: Account) -> ArticleAttentionConditions`.
  - `ac_service.get_articles_attention_conditions(article_ids: Iterable[int], user: Account, journal: Journal)
    -> dict[int, ArticleAttentionConditions]` — one query; articles without ACs map to an empty `ArticleAttentionConditions`.
  - `ArticleAttentionConditions.legacy_message -> str`: message of the highest-priority row among `active` + `unread`
    (by `priority`, `created_at`), `""` if none — reproduces today's `_prefetched_acs` value.
  - Template filter `article_attention_conditions(workflow, user) -> ArticleAttentionConditions`: uses `workflow._prefetched_attention_conditions`
    (a `dict[int, ArticleAttentionConditions]`) when set, else `get_article_attention_conditions`.
  - Listing: for `context["workflows"]` (the current page) set both `_prefetched_attention_conditions` and the legacy
    `_prefetched_acs` (`article_id → legacy_message`).
  - Detail: `context["attention"] = get_article_attention_conditions(workflow.article, request.user)`.

- [ ] **Step 1: Write the failing tests**
  - `test_article_attention_splits_active_dismissed_unread`: one active, one dismissed, one unread for the editor →
    lists/unread as expected, `has_active` True; dismiss the active one and resolve the unread → `has_active`
    False, `has_any` True.
  - `test_article_attention_marks_fallback_rows_not_dismissible`: the Task 3 fallback EO user → `dismissible`
    False; an EO human with own rows → True.
  - `test_articles_attention_batch_matches_single`: two articles → batch dict equals per-article results.
  - `test_article_attention_filter_uses_prefetch`: with `_prefetched_attention_conditions` set, the filter does no query
    (`django_assert_num_queries(0)`).
  - `test_listing_prefetch_limited_to_current_page`: editor listing (`reverse` of the editor pending view) with
    `paginate_by` patched to 1 and two articles with ACs → only the page's article id in the prefetched dict;
    legacy `_prefetched_acs` value equals the highest-priority active message; a dismissed-only article has
    `""` in the legacy dict.
  - `test_detail_context_has_attention`: GET `wjs_article_details` as the editor → `response.context["attention"]`
    is an `ArticleAttentionConditions` with the expected ACs.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per Interfaces. Replace the listing's "all active ACs of the user" query with
  `get_articles_attention_conditions(ids_of_page, user, request.journal)`. Update the comment block above
  `article_requires_attention_tt` (it still describes the single-message path; say it is legacy now).
- [ ] **Step 4: Run, expect PASS**, plus the existing listing/detail tests touching ACs (`-k "attention"`).
- [ ] **Step 5: Commit** — `feat(wjs_review): expose active, dismissed and unread attention conditions to templates`

---

### Task 6: HTMX toggle endpoint and minimal panel

**Files:**
- Modify: `wjs/plugins/wjs_review/views.py` (new view next to `ToggleMessageReadView`, ~2783)
- Modify: `wjs/plugins/wjs_review/urls.py` (next to `wjs-article-attention-conditions`, ~281)
- Create: `wjs/plugins/wjs_review/templates/wjs_review/details/sections/attention_panel.html`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/details/articleworkflow_detail.html` (include)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Consumes: `ToggleAttentionConditionDismissalForm` (Task 4), `get_article_attention_conditions` (Task 5).
- Produces:
  - URL `status/<int:pk>/attention-conditions/<int:ac_pk>/toggle/`, name `toggle-attention-condition`.
  - `ToggleAttentionConditionDismissalView(HtmxMixin, AuthenticatedUserPassesTest, UpdateView)`:
    `http_method_names = ["post"]`; `get_object` = AC with `pk=ac_pk` and `article=workflow(pk).article`
    (404 otherwise); `test_func`: `object.user_id == request.user.pk`; passes `user` to the form;
    `form_valid` wraps `super().form_valid` in `try/except ValidationError → form_invalid`; both valid and invalid
    render `attention_panel.html` (no redirect) with context `workflow`, `attention`, `show_all`
    (`request.POST.get("show_all") == "1"`), `form`.
  - Panel template: root `<div id="attention-panel">`; renders nothing inside when `not attention.has_any`;
    lists `attention.active` (Dismiss button if `ac.dismissible`), `attention.dismissed` (Dismissed button if
    `ac.dismissible`), unread row linking `{% url "wjs_article_messages" pk=workflow.pk %}`; buttons use
    `hx-post` to the toggle URL, `hx-target="#attention-panel"`, `hx-swap="outerHTML"`,
    `hx-vals='{"show_all": "{{ show_all|yesno:"1,0" }}"}'`; form non-field errors shown. Plain Bootstrap, no design.
  - Detail page: include the panel **only when `request.GET.ac_panel` is set** (testing hook for QA until the
    frontend lands; no visible change for users). State this in the MR description.

- [ ] **Step 1: Write the failing tests**
  - `test_toggle_view_dismisses_and_restores`: editor POSTs twice → 200, status flips, response contains the
    AC message and `id="attention-panel"`.
  - `test_toggle_view_rejects_foreign_or_mismatched_ac`: another user → 403; `ac_pk` of an AC on another article
    with this workflow's `pk` → 404; GET → 405; status unchanged in all cases.
  - `test_toggle_view_unread_ac_renders_error`: owner POSTs on own `HAS_UNREAD_MESSAGE` → 200, status unchanged,
    `response.context["form"].non_field_errors()` not empty.
  - `test_toggle_view_echoes_show_all`: POST `show_all=1` → `response.context["show_all"] is True`.
  - `test_detail_page_includes_panel_only_with_query_param`: without `?ac_panel=1` → no `id="attention-panel"`;
    with it → present.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run, expect PASS**; `pre-commit run djlint-django --files <template>`.
- [ ] **Step 5: Commit** — `feat(wjs_review): add htmx endpoint to dismiss and restore attention conditions`

---

### Task 7: Plugin documentation

**Files:**
- Modify: `wjs/plugins/wjs_review/CLAUDE.md`

- [ ] **Step 1:** Remove the description of `BaseState.article_requires_attention`; describe the materialized AC
  lifecycle (ACTIVE / DISMISSED / RESOLVED, what `upsert_ac` and the resolvers do), the EO visibility rule with
  fallback, the read path (`ArticleAttentionConditions`, `article_attention_conditions` filter, legacy filter), the toggle endpoint and
  the `?ac_panel=1` testing hook, and the known gaps (stale rows from previous states, role loss).
- [ ] **Step 2:** `pre-commit run --all-files` → all hooks pass.
- [ ] **Step 3: Commit** — `docs(wjs_review): document attention condition dismissal`
