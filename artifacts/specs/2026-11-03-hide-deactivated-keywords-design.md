# Hide deactivated keywords from keyword lists and selection pages

- **Issue:** wjs/specs#3279
- **Project:** wjs-profile-project, `wjs/jcom_profile` (reads `get_keywords_by_journal` from the sibling `wjs-submission-project`)
- **Date:** 2026-11-03

## Intent

A keyword with `Keyword.deactivated` set is retired: it must no longer be advertised or offered for
selection, but articles already tagged with it must stay findable. Today deactivated keywords still show
on the public keywords page and are still offered as newsletter topics, profile interests and editor
assignment keywords (only partly hidden on hierarchical journals, since wjs-submission `62a6972`).

## Goals

1. Public keywords page (`/keywords/`, `KeywordListView`) lists only active keywords, for every journal
   layout: JCOM/JCOMAL plain list, non-hierarchical first-letter accordion (e.g. JCAP), and hierarchical
   accordion (JQuant, JHEP), including the "Other keywords" (ungrouped) block. Groups/subgroups with no
   active keyword are not rendered (already handled by `get_keywords_by_journal`).
2. Keyword-selection pages built on `KeywordsHierarchyContextMixin` offer only active keywords, in both the
   flat and the hierarchical rendering:
   - newsletter preferences (`NewsletterParametersUpdate` / `NewsletterTopicForm`);
   - profile interests (`ProfileInterestsEditView` / `WjsInterestsForm`);
   - editor assignment parameters (`StaffWorkloadParametersUpdate` / `UpdateAssignmentParametersForm`).
3. New newsletter recipients get only active keywords as default topics: `NewsletterParametersUpdate.get_object`
   (token and logged-in branches) and `AnonymousUserNewsletterRegistration.form_valid` (logged-in branch).
4. Already-selected deactivated keywords (decided by us, pending confirmation on the issue):
   - newsletter topics and profile interests: silently dropped the next time the user saves the page
     (falls out naturally from the filtered form querysets; no extra code);
   - editor assignment keywords: **kept** on save, not shown. The `StaffKeyword` rows pointing to
     deactivated keywords are neither deleted nor recreated, so their director-managed `weight` survives.

## Non-goals

- Search page keyword filter (`SearchForm`): unchanged, keeps offering keywords of published articles,
  deactivated included.
- Submission keyword selection: already filters deactivated keywords.
- Director-side editor keyword management (weights page) and the admin article-keywords form
  (`wjs/jcom_profile/forms.py`, form around line 649): unchanged.
- No data migration: existing topics/interests/staff keywords are not cleaned up proactively.
- Newsletter sending: unchanged (no new articles can carry a deactivated keyword).

## Anti-goals

- An editor losing an assignment keyword (or its weight) just by saving their parameters page.
- Changing behaviour of journals/pages for active keywords (ordering, `word=""` exclusion, grouping).

## Constraints / decided how

- Single source of truth for "active": `deactivated__isnull=True`, the same condition wjs-submission's
  `_selectable_keywords` uses. A small shared helper in `wjs/jcom_profile` is the builder's call; reusing
  `_selectable_keywords` (private, other package) is not required. *(Our call:)* `Keyword` is a Janeway
  model, so the "filter through a custom model queryset" architecture rule can't be applied without
  monkeypatching; a plain filter/helper is acceptable here.
- `KeywordListView.get_queryset`: add `deactivated__isnull=True`.
- `KeywordsHierarchyContextMixin`: filter `ungrouped_keywords` with `deactivated__isnull=True`;
  `keywords_list` is already filtered.
- Form querysets restricted to active keywords: `NewsletterTopicForm`, `WjsInterestsForm`,
  `UpdateAssignmentParametersForm`. This also covers the flat template branch, which renders from
  `form.keywords`.
- `UpdateAssignmentParametersForm.save`: the final `StaffKeyword ... .exclude(keyword__in=kwds).delete()`
  must also exclude deactivated keywords.
- Templates (`journal/keywords.html`, `wjs/profile/_keywords_selection.html`): no change expected; they read
  the prefetched, filtered querysets (`.all` / `.exists` hit the prefetch cache).
- Tests with pytest in `wjs/jcom_profile/tests/` (existing: `test_views.py`, `test_newsletters.py`,
  `test_staff_parameters.py`), TDD.

## Left to the builder

- Exact test split/naming, fixtures for a deactivated keyword (set `deactivated=timezone.now()`).
- Whether to add a shared helper or inline the filter.

## Functional test steps (proposed update for the issue)

1. On JCOM and on JCAP (test env), as admin, deactivate a keyword used by at least one published article.
2. `/keywords/` as anonymous: the keyword is not listed; the others are.
3. Newsletter preferences (logged in, and via token link): the keyword is not offered.
4. Profile interests: the keyword is not offered.
5. Editor assignment parameters of an editor who had that keyword: not shown; after saving, the editor
   still has it (director view / DB).
6. Search page: the keyword is still a filter and returns its articles (where the filter is shown).
7. Repeat 2–5 on a hierarchical journal (JQuant or JHEP), deactivating an ungrouped keyword and all
   keywords of one subgroup: neither the keyword nor the empty subgroup is shown.
8. Reactivate: the keyword is listed/offered again everywhere.
