# "View only with Action Required" as a backend filter — design

Status: approved (decisions updated after the whole-branch review)
Issue: wjs/specs#3482 (MR must use `main: #3482`, no closing keywords). #3035 is merged into it and closed.
Size: small change
Documentation language: English (confirmed for this flow)

## Intent

The "View only with Action Required (current page)" button on the paper lists hides, in the browser, the rows of the
current page that are not highlighted. It cannot find papers with an attention condition (AC) on other pages. The EO
clicks it page after page, and appeals on late pages of the archived list can be missed (#3457). ACs are now stored in
the database, so the filter can work on the whole list like every other filter. On the EO "Pending" list it is also the
default for an EO who is automatically assigned papers, like "EO in charge" (#3035).

## Goals

- A backend filter over the whole list: a paper matches when the current user has on it an AC that is active, unread
  messages included, by the same rule that highlights the row (`AttentionConditionQuerySet.active().visible_to()`).
- The same two-button group ("View All" / "View only with Action Required") on every list that has the filter form
  today (not the author and reviewer Pending lists, which have `show_filters = False`), state kept in the page address
  (pagination, ordering, reload and links keep it; this also solves #1606).
- Default on `EOPending` for an EO with a workload, set together with "EO in charge". An explicit choice always wins.
- Every list query carries a `has_ac` annotation, so templates and later controls (for example ordering) can use it.

## Non-goals / anti-goals

- No change to how ACs are created, dismissed or expire (#3311), nor to icons, popovers and row highlight.
- No "EO Todo" prepopulated search, "Pending bis" page or dismissed-AC list (they stay on #3457).
- Failure modes to avoid: "View All" doing nothing because the default is applied again; the filter and the highlight
  disagreeing on which papers need attention; more than the current 5 queries per list page.

## Constraints

- `visible_to` is the only definition of "ACs the user can see" (own ACs, plus the EO system user's rows for EO
  people); it already contains an `OuterRef("article_id")`, so its nesting inside an `Exists` on the workflow must be
  checked.
- `get_queryset` already annotates with a `Max` and ends with `.distinct()`; pagination counts must stay correct.
- Filtering belongs in a queryset method (`.claude/rules/architecture-django.md`), not in the view or template.
- The default must keep `EOPending.prefilter_by_eo` semantics: only on `EOPending`, only for
  `has_eo_with_workload_role`.

## Approaches

Measured on `janeway_jcap` (3,903 workflows, 1,231 active ACs), read-only, median of 9 runs, EO with 184 active ACs.

| | Page load (ms), Pending / Archived / Search | Queries per page |
|---|---|---|
| Baseline (no filter) | 13.8 / 23.8 / 30.3 | 5 |
| A. Filter only: `Exists` applied only when the filter is chosen | 11.4 / 7.6 / 14.9 (filter on) | 5 |
| B. Annotate every row with `has_ac`, filter on it | 14.6 / 22.4 / 32.7 (filter off); 12.1 / 8.5 / 16.0 (filter on) | 5 |

- Ordering by `has_ac` first (only possible with B) adds about 3.5 ms on the 3,191-row archived list and under 1 ms
  on the others.
- The views already prefetch the page's ACs in one query, so neither option saves queries there. B only matters if the
  prefetch is missing: the per-row fallback of `article_attention_conditions` costs 4 queries per row (200 per 50
  rows), and with B only the rows that have an AC would pay.
- Chosen: **B**. It costs 1 to 3 ms per page, which the user judged negligible, and gives the annotation to other
  controls (decided by the user, 2026-11-24).

## Decisions

- One queryset method on `ArticleWorkflowQuerySet` annotates `has_ac`; the filter and the annotation share it.
- `BaseArticleWorkflowFilter` gets the boolean filter, so author, reviewer, staff, EO and work-on filtersets inherit
  it. The inline script in `filters_visibility.html` is removed from all four filter templates.
- `ArticleWorkflowBaseMixin.get_queryset` always adds the annotation; `lists/elements/attention.html` calls
  `article_attention_conditions` only when `has_ac` is true.
- Buttons (my call): "View only…" submits `action_required=1`, "View All" submits `action_required=0`, as submit
  buttons in the existing GET form; the button state is rendered from the parameter. `EOPending.load_initial` adds the
  default only when neither `search` nor `action_required` is in the querystring. "Reset filters" returns to the
  default, as it does for "EO in charge".
- Added after the whole-branch review:
  - the EO default stores `eo_in_charge` as a pk (not the `Account`), so the sort and pagination links built from the
    querystring stay valid; an explicit `action_required` cancels both defaults;
  - a hidden input carries the current choice, so Search and Enter keep it (a clicked button, later in the form, wins);
  - the filter reuses the `has_ac` annotation when the queryset has it, instead of building the rule a second time.
- Measured on the real code (JCAP, View All): list pages run 7 queries instead of 5, +2 constant (`has_eo_role` and
  `get_eo_user` inside `visible_to`, now also run by the annotation), about +2 to 4 ms. The earlier A/B measurement missed
  them (the rule was built outside the timed loop). Open decision for the user: accept, or memoize the EO lookups per
  request in a follow-up.
- Row highlight stays on `article_requires_attention_tt` (my call): moving it to `has_ac` touches six row templates
  and the legacy prefetch, and is not needed here. Both come from the same rule.
- The wiki page `Review/AttentionConditions` already carries LST-10 to LST-12 for this behaviour (written with the
  spec of #3482); no further wiki change is planned.

## Left to the builder

- Parameter and method names, the exact queryset method signature, and how the button state is rendered.
- Whether `has_ac` is also exposed to the other listing views that do not use `ArticleWorkflowBaseMixin`.
- Test layout: filterset tests (pattern in `tests/test_filterset.py`), AC fixtures
  (`ac_service.upsert_ac`, as in `tests/test_attentionconditions.py`), view tests for the default and for "View All",
  and a query-count check that a list page keeps 5 queries.
- The functional steps T-1 to T-8 of the issue are the acceptance reference; update them on delivery if the
  implementation differs.
