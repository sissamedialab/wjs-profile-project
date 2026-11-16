# Hide Deactivated Keywords Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop listing/offering deactivated keywords on the public keywords page and on the newsletter,
profile-interests and editor-parameters selection pages, keeping editors' already-selected deactivated keywords.

**Architecture:** One helper returns a journal's active keywords (`deactivated IS NULL`); the list view, the
shared `KeywordsHierarchyContextMixin`, the three selection forms and the newsletter default-topic code use it.
`UpdateAssignmentParametersForm.save` stops deleting `StaffKeyword` rows of deactivated keywords. Templates are
unchanged (hierarchical tree already filtered by wjs-submission's `get_keywords_by_journal`).

**Tech Stack:** Django (inside Janeway), pytest + pytest-django.

**Spec:** `artifacts/specs/2026-11-03-hide-deactivated-keywords-design.md`

## Global Constraints

- "Active" means `deactivated__isnull=True` — nothing else (any non-null timestamp = deactivated).
- Search (`SearchForm`), submission, `DirectorStaffWorkloadParametersUpdate` (weights formset) and the admin
  article-keywords form are not touched.
- No data migration; no template changes.
- Existing behaviours kept: ordering by `word`, `exclude(word="")` where already present.
- Black/isort/flake8/pydocstyle at line length 119; English docstrings; double quotes.
- Tests run from `janeway/src`: `pytest ../../wjs-profile-project/<path>` (add `--create-db` the first time).

## Review Focus

1. Editor saving parameters while holding a deactivated keyword: the `StaffKeyword` row and its `weight`
   survive (test in Task 4).
2. Hierarchical journal: a subgroup whose keywords are all deactivated is not rendered, and a deactivated
   ungrouped keyword is not in "Other keywords" (test in Task 1).
3. Token-based (anonymous) newsletter recipient with no topics opening the edit page gets only active
   keywords as defaults (test in Task 2).
4. A POST carrying a deactivated keyword's pk to a selection form is rejected as an invalid choice, not saved
   (test in Task 2, newsletter form; same queryset mechanism covers the others).
5. Reactivating (`deactivated=None`) makes the keyword appear again — covered implicitly by the helper test.

## Shared test setup

All new tests go in a new file `wjs/jcom_profile/tests/test_deactivated_keywords.py`, using existing
fixtures from `wjs/jcom_profile/tests/conftest.py`: `journal`, `keywords` (10 keywords `"0-keyword"`…
`"9-keyword"` linked to `journal`), `jcom_user`, `section_editor`, `roles`, `eo_group`.
Deactivate with `Keyword.objects.filter(pk=kw.pk).update(deactivated=timezone.now())`.
Hierarchical setup (local fixture `hierarchical_journal`): set
`journal.submissionconfiguration.hierarchical_keywords = True` and save; create
`KeywordGroup(name="Group A", order=1)` with subgroups `"Sub A1"` (`parent_group=Group A`, keywords 0–1) and
`"Sub A2"` (keywords 2–3), set `Keyword.group` accordingly; keywords 4–9 stay ungrouped.

---

### Task 1: Active-keywords helper, public keywords page, mixin

**Files:**
- Modify: `wjs/jcom_profile/utils.py` (add helper)
- Modify: `wjs/jcom_profile/views.py:907-908` (`KeywordListView.get_queryset`)
- Modify: `wjs/jcom_profile/profile/views.py:161-163` (`ungrouped_keywords`)
- Test: `wjs/jcom_profile/tests/test_deactivated_keywords.py`

**Interfaces:**
- Produces: `get_active_keywords(journal: Journal) -> QuerySet[Keyword]` in `wjs/jcom_profile/utils.py`,
  returning `journal.keywords.filter(deactivated__isnull=True)` (unordered; callers order).

- [ ] **Step 1: Write failing tests**
  - `test_get_active_keywords_excludes_deactivated(journal, keywords)`: deactivate `"0-keyword"`;
    assert `"0-keyword"` not in words and the 9 others are; set `deactivated=None` again → present.
  - `test_keywords_list_page_hides_deactivated_flat(client, journal, keywords)`: deactivate `"0-keyword"`,
    GET `reverse("keywords_list")` (journal URL as other tests do, `f"/{journal.code}/keywords/"`);
    assert `list(response.context["keywords"])` excludes it and includes `"1-keyword"`.
  - `test_keywords_list_page_hides_deactivated_hierarchical(client, hierarchical_journal, keywords)`:
    deactivate keywords 2, 3 (all of `"Sub A2"`) and 4 (ungrouped); GET the page; assert
    `"Sub A2"` not in content, `"Sub A1"` and `"0-keyword"` in content, `"4-keyword"` not in
    `response.context["ungrouped_keywords"]`, `"5-keyword"` is.

- [ ] **Step 2: Run, verify they fail**
  `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_deactivated_keywords.py -v` → FAIL
  (ImportError for the helper; deactivated keywords present).

- [ ] **Step 3: Implement** the helper; `KeywordListView.get_queryset` → `get_active_keywords(self.request.journal).order_by("word")`;
  mixin `ungrouped_keywords` → `get_active_keywords(journal).filter(group__isnull=True).exclude(word="").order_by("word")`.

- [ ] **Step 4: Run, verify pass** (same command) → PASS.

- [ ] **Step 5: Commit** (per the commit strategy chosen at flow step 4)
  `fix(keywords): hide deactivated keywords from the public keywords page`

### Task 2: Newsletter topics

**Files:**
- Modify: `wjs/jcom_profile/newsletter/forms.py:57` (`NewsletterTopicForm` queryset)
- Modify: `wjs/jcom_profile/newsletter/views.py:55,62,105` (default topics)
- Test: `wjs/jcom_profile/tests/test_deactivated_keywords.py`

**Interfaces:**
- Consumes: `get_active_keywords` (Task 1).

- [ ] **Step 1: Write failing tests**
  - `test_newsletter_form_offers_only_active_keywords(journal, keywords, jcom_user)`: deactivate
    `"0-keyword"`; build `NewsletterTopicForm(instance=recipient, initial={})` for a `Recipient(user=jcom_user, journal=journal)`;
    assert it's not in `form.fields["keywords"].queryset`.
  - `test_newsletter_form_rejects_deactivated_keyword(...)`: bound form with `keywords=[deactivated.pk]`,
    `news=True` → `not form.is_valid()` and `"keywords"` in `form.errors`.
  - `test_new_logged_in_recipient_gets_only_active_topics(client, journal, keywords, jcom_user)`:
    deactivate `"0-keyword"`, `client.force_login(jcom_user)`, GET `f"/{journal.code}/update/newsletters/"`;
    recipient's `topics` == the 9 active keywords.
  - `test_token_recipient_without_topics_gets_only_active_topics(client, journal, keywords)`:
    `Recipient(email=..., journal=journal, newsletter_token="tok", news=False)` no topics; GET with `?token=tok`;
    topics == 9 active keywords.
  - `test_logged_in_registration_gets_only_active_topics(client, journal, keywords, jcom_user)`:
    logged in, POST `f"/{journal.code}/register/newsletters/"` (create the `privacy` `Page` as in
    `test_newsletters.py:629-630` if needed); new recipient's topics == 9 active keywords.

- [ ] **Step 2: Run, verify they fail** (same command, `-k newsletter or recipient or registration`).

- [ ] **Step 3: Implement**: form queryset `get_active_keywords(self.journal).order_by("word")`; the three
  `recipient.topics.set(recipient.journal.keywords.all())` → `get_active_keywords(recipient.journal)`.

- [ ] **Step 4: Run, verify pass**; also run `wjs/jcom_profile/tests/test_newsletters.py` → PASS (no regressions).

- [ ] **Step 5: Commit** `fix(newsletter): offer only active keywords as newsletter topics`

### Task 3: Profile interests

**Files:**
- Modify: `wjs/jcom_profile/profile/forms.py:235` (`WjsInterestsForm` queryset)
- Test: `wjs/jcom_profile/tests/test_deactivated_keywords.py`

- [ ] **Step 1: Write failing test** `test_interests_form_offers_only_active_keywords(journal, keywords, jcom_user)`:
  deactivate `"0-keyword"`; `WjsInterestsForm(instance=jcom_user, journal=journal, initial={})`; assert not in
  `form.fields["keywords"].queryset`, `"1-keyword"` is.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement**: `get_active_keywords(self.journal).exclude(word="").order_by("word")`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `fix(profile): offer only active keywords as profile interests`

### Task 4: Editor assignment parameters

**Files:**
- Modify: `wjs/jcom_profile/forms.py:213` (queryset) and `:233` (final delete in `save`)
- Test: `wjs/jcom_profile/tests/test_deactivated_keywords.py`

- [ ] **Step 1: Write failing tests**
  - `test_assignment_parameters_form_offers_only_active_keywords(journal, keywords, section_editor)`:
    deactivate `"0-keyword"`; form for the editor's `StaffWorkloadParameters`; not in queryset.
  - `test_assignment_parameters_save_keeps_deactivated_staff_keyword(client, journal, keywords, section_editor, roles, eo_group)`:
    editor has `StaffKeyword` for `"0-keyword"` (`weight=7`) and `"1-keyword"`; deactivate `"0-keyword"`;
    `client.force_login(section_editor)`, POST `f"/{journal.code}/update/parameters/"` with
    `{"keywords": [pk of "2-keyword"], "workload": 10}` → 302; assert `StaffKeyword` for `"0-keyword"` still
    exists with `weight == 7`, `"1-keyword"` row deleted, `"2-keyword"` row created.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement**: queryset `get_active_keywords(self.instance.journal).order_by("word")`; final delete
  `.exclude(keyword__in=kwds).exclude(keyword__deactivated__isnull=False).delete()` with a one-line comment why
  (hidden deactivated keywords and their director-set weights are kept).
- [ ] **Step 4: Run, verify pass**; also `pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_views.py -k "parameters or keyword"`
  and `wjs/jcom_profile/tests/test_staff_parameters.py` → PASS.
- [ ] **Step 5: Commit** `fix(editor): keep deactivated assignment keywords hidden but preserved`

### Task 5: Final check

- [ ] `pytest ../../wjs-profile-project/wjs/jcom_profile/tests -n7` → all PASS.
- [ ] `pre-commit run --all-files` (from the repo root) → clean.
