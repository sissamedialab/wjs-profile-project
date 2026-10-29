# Design: reconcile editor assignment with the "Editor Selection logic" wiki (rev 1.1.3)

## Context

`wjs/plugins/wjs_review/events/assignment.py` was last changed by the plan at
`docs/superpowers/plans/2026-09-17-automatic-assignment-keyword-matching.md`
(commits `2b9e345c`..`3a6293c8`), which ported keyword matching into the
*automatic* assignment path. That plan was built against the wiki
[Editor-Selection-logic](https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic)
as it read on 2026-09-17, and explicitly recorded several "confirmed by repo
owner" readings of ambiguous wording — most importantly, that keyword
matching **hard-filters** non-matching editors out, with a fallback to
`get_selected_editor_by_workload` when the filter empties the pool.

Between then and now the wiki was revised four times (1.1 → 1.1.1 → 1.1.2 →
1.1.3, all 2026-09-22, all "Clarify filtering/sorting") specifically to
remove that ambiguity, landing on the **opposite** reading: keyword matching
never excludes, it only ranks. As of 1.1.3 the wiki's own "Implementation
details" subsections agree with the narrative sections throughout — see
"Wiki staleness — resolved" below; there is no more residual contradiction
to track. A companion review of the current code against the now-clarified
wiki (this conversation, same session) surfaced five more gaps beyond the
keyword-filter question. This document is the design for closing all of
them. It **supersedes** the keyword-filtering behavior built by the
2026-09-17 plan; everything else that plan did (the `brake_on`/`workload`
cap logic, the special-issue/JCOM bypasses, the overall file structure) is
kept.

No new implementation plan/TDD task list exists yet for this — see
"Follow-up" at the end.

## Findings this design addresses

1. Manual assignment (`SupervisorAssignEditor._editors_with_keywords`)
   doesn't apply availability filtering or workload annotation at all, and
   ranks only by keyword *count*, never weight.
2. The monthly workload cap counts `WjsEditorAssignment` rows regardless of
   the article's current state, so a withdrawn paper still eats into an
   editor's cap. Deselected editors are unaffected (see below) — only the
   "withdrawn" exclusion is missing.
3. Filtering and ranking currently share one workload metric (calendar
   month); the wiki specifies two distinct ones (calendar-month cap for
   filtering, trailing-30-day window for ranking), and the "manual"
   variant of the workload annotation was never built.
4. **(resolved by the wiki edits, addressed here in code)** Keyword
   matching must never exclude a candidate — `filter_parameters_by_matching_keywords`
   currently does, via `.filter(keywords__in=...)` / `.exclude(matching_keywords_count=0)`.
5. `filter_parameters_by_matching_keywords` and
   `annotate_automatic_assignment_workload` each end in their own
   `.order_by(...)`, silently discarded by the caller's own final
   `.order_by(...)`. Harmless today (single call site), a footgun for the
   next caller (i.e. finding 1's fix).
6. **(new, found while designing the fix for 1; resolved — see D6)**
   Automatic assignment restricts candidates to `SECTION_EDITOR_ROLE` only;
   manual's `Account.objects.get_editors_with_keywords` includes
   `EDITOR_ROLE` too. Decision: manual assignment drops `EDITOR_ROLE` and
   matches automatic assignment's role set.

Explicitly **not** addressed here (unchanged from the 2026-09-17 plan's
scope, still marked Priority 2 or bypass-only in the wiki):
`Disable silently`, the scoring/report feature, "Residual workload",
per-editor statistics, pending-paper counts, and the `#1253` custom
exceptions. Also unchanged: the special-issue bypass
(`get_special_issue_parameters`) and the JCOM/JCOMAL director bypass
(`jcom_assign_editors_to_articles`) — both remain keyword-blind per the
wiki's "bypasses" note.

## Design

### D1 — Keyword ranking becomes annotate-only (closes finding 4)

Rename `filter_parameters_by_matching_keywords` → `annotate_keyword_ranking`
(the old name is actively misleading once it stops filtering — and this is
now also the wiki's own name for it, its "Implementation details → Keywords
matching" sample was rewritten in rev 1.1.3 to match, see "Wiki staleness —
resolved" below).

**Correction to an earlier draft of this section:** the actual current
implementation (commit `3a6293c8`, postdating the 2026-09-17 plan) does
**not** compute `Count`/`Max` directly against `StaffWorkloadParameters` —
it delegates to `Account.objects.get_editors_with_keywords(...)` (the same
function the manual view uses) and pulls the two values back onto
`StaffWorkloadParameters` via `Subquery`, specifically so keyword-counting
logic exists in exactly one place. Reimplementing it inline here (as an
earlier draft of this section did) would reintroduce the duplication that
refactor removed. Keep the delegation; only remove the two exclusion steps:

```python
def annotate_keyword_ranking(
    article: Article,
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """Annotate parameters with keyword-match count/weight against the article. Never excludes."""
    annotated_accounts = Account.objects.filter(
        pk__in=users_parameters.values_list("user_id", flat=True),
    ).get_editors_with_keywords(article, include_matching_keywords=False)
    return users_parameters.annotate(
        matching_keywords_count=Coalesce(
            Subquery(annotated_accounts.filter(pk=OuterRef("user_id")).values("num_shared_kwds")[:1]), 0,
        ),
        max_matching_keyword_weight=Subquery(
            annotated_accounts.filter(pk=OuterRef("user_id")).values("max_matching_keyword_weight")[:1],
        ),
    )
```

Diff from today's `filter_parameters_by_matching_keywords`: drop
`.exclude(num_shared_kwds=0)` on `annotated_accounts` (today's code calls
this variable `matched_accounts`); drop `.filter(matching_keywords_count__isnull=False)`
on the returned queryset; wrap the count in `Coalesce(..., 0)` so a
non-matching parameter reads `matching_keywords_count=0` rather than `NULL`
(`num_shared_kwds` is a plain `Count(...)`, which is already 0 for no
matches — the `Coalesce` only guards the `Subquery`'s own NULL-when-no-row
case, now reachable since non-matches are no longer excluded from
`annotated_accounts`); drop the trailing `.order_by(...)` (see D3). Leave
`max_matching_keyword_weight` as a bare `Subquery` — `None` for a
non-match is exactly the value D3's `nulls_last=True` ordering needs.

### D2 — One workload-annotation function, two windows, no built-in filter (closes findings 2, 3)

Replace `annotate_automatic_assignment_workload` with
`annotate_workload_availability`. It computes the shared `effective_workload_cap`
(`min(workload, brake_on)` when `brake_on > 0`, else `workload` — unchanged
from today) once, then annotates **two** counts against it, per the wiki's
"Workload" section:

- `available_workload_month` — calendar-month count (today's metric), used
  by automatic assignment's **filtering** step and shown as "available
  monthly workload" in both automatic's (deferred) report and manual's
  required "Values shown" list.
- `available_workload_30d` — trailing-30-day rolling count, used purely as
  the **ranking** tie-break by both automatic and manual assignment (the
  wiki's "Ranking" subsection, and "[manual] sorted according to the same
  rules as automatic assignment").

Both counts must **exclude withdrawn papers** (closes finding 2 — today's
count doesn't). Deselected editors need no explicit exclusion:
`BaseDeassignEditor._delete_assignment` (`logic.py:3798-3816`) `.delete()`s
the `WjsEditorAssignment` row outright and moves history to
`PastEditorAssignment`, so a deselected assignment is already gone from
`WjsEditorAssignment` by construction — the "or from which the editor has
been deselected" clause in the wiki's "User profile configuration" section
is already satisfied by the data model, nothing to add for it.

The function does **not** filter — that's a call-site decision (see D-Automatic
and D-Manual below), which is also how the wiki's own wording
("annotate the queryset with automatic **and manual** assignment workloads")
is read here: one shared annotation function serving both consumers, not two
separate metrics named "automatic" and "manual".

```python
def annotate_workload_availability(
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Annotate parameters with monthly-cap and trailing-30-day workload availability.

    Both counts exclude WITHDRAWN articles. Deselected assignments need no explicit
    exclusion: BaseDeassignEditor deletes the WjsEditorAssignment row on deselection.
    Never filters — callers decide whether/how to apply available_workload_month as
    an exclusion (see default_assign_editors_to_articles vs. the manual selection view).
    """
    from ..models import ArticleWorkflow, WjsEditorAssignment

    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    window_30d = timezone.localtime() - datetime.timedelta(days=30)
    not_withdrawn = ~Q(article__articleworkflow__state=ArticleWorkflow.ReviewStates.WITHDRAWN)

    count_month = (
        WjsEditorAssignment.objects.filter(
            not_withdrawn, editor=OuterRef("user_id"), article__journal=OuterRef("journal_id"),
            assigned__gte=month_start,
        ).annotate(count=Func(F("id"), function="Count")).values("count")
    )
    count_30d = (
        WjsEditorAssignment.objects.filter(
            not_withdrawn, editor=OuterRef("user_id"), article__journal=OuterRef("journal_id"),
            assigned__gte=window_30d,
        ).annotate(count=Func(F("id"), function="Count")).values("count")
    )
    return users_parameters.annotate(
        assignment_count_month=Coalesce(Subquery(count_month), 0),
        assignment_count_30d=Coalesce(Subquery(count_30d), 0),
        effective_workload_cap=Case(
            When(brake_on__gt=0, then=Least(F("workload"), F("brake_on"))), default=F("workload"),
        ),
        available_workload_month=F("effective_workload_cap") - F("assignment_count_month"),
        available_workload_30d=F("effective_workload_cap") - F("assignment_count_30d"),
    )
```

### D3 — Shared final ordering (closes finding 5)

```python
def rank_editor_parameters(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]:
    """Canonical final ordering: keyword weight, then keyword count, then 30-day workload, then id."""
    return users_parameters.order_by(
        F("max_matching_keyword_weight").desc(nulls_last=True),
        "-matching_keywords_count",
        "-available_workload_30d",
        "id",
    )
```

**Pitfall to test for explicitly:** plain `"-max_matching_keyword_weight"`
sorts `NULL` *first* in Postgres under `DESC`, which would put
no-keyword-match editors **ahead** of matched ones — backwards. Must use
`F(...).desc(nulls_last=True)`, exactly like the existing
`get_select_eo_by_workload` (`assignment.py:342`) already does for its own
`available_workload` ordering. This is the single most likely regression
this change could introduce if skipped.

### D4 — `default_assign_editors_to_articles`: drop the fallback branch

With D1 removing keyword-based exclusion, the pool is no longer emptied by
a lack of keyword matches specifically (previously the fallback existed
*because* that filter could empty it). **Correction, found in final review:**
the pool can still end up empty for an unrelated reason — the
`available_workload_month__gt=0` filter (D2/D2's monthly cap) can exclude
every remaining candidate if all of them are at capacity — so
`default_assign_editors_to_articles` can still return `None` with no
assignment made. That's an accepted, correctly-handled outcome (the article
falls through to `EDITOR_TO_BE_SELECTED` + director reminders, exactly the
existing "no editors configured at all" path), not a bug, but the earlier
draft of this section overstated the guarantee — flagging it explicitly here
so it isn't mistaken for "can never happen." New pipeline for the
non-special-issue branch:

```python
parameters = get_available_editor_parameters(journal=article.journal, editors=editors)
parameters = parameters.exclude(user__in=article.author_accounts.all())
if parameters:
    ranked = rank_editor_parameters(
        annotate_workload_availability(annotate_keyword_ranking(article, parameters))
        .filter(available_workload_month__gt=0)
    )
    if matched := ranked.first():
        user = matched.user
```

The `available_workload_month__gt=0` filter is the automatic-only exclusion
step the wiki's "Automatic Editor assignment function" section calls out
("exclude editors with a negative available 'calendar month' workload") —
it is **not** applied in the manual view (D6). `get_selected_editor_by_workload`
is no longer called from this function at all; it remains used by
`jcom_assign_editors_to_articles` (untouched, still keyword-blind per the
JCOM/JCOMAL bypass) and stays exactly as-is otherwise.

Special-issue and JCOM bypass branches: unchanged, per the wiki's own
"bypasses" note — not revisited by this design.

### D5 — Manual selection view intersects with the same availability pipeline (closes finding 1)

**Correction to an earlier draft of this design:** the current code (as of
commit `3a6293c8`, "unify keyword-matching logic between manual and
automatic assignment" — postdating the 2026-09-17 plan this design
otherwise supersedes) already has `annotate_keyword_ranking`'s predecessor
call `Account.objects.get_editors_with_keywords(...)` internally to compute
the keyword annotation, rather than annotating `StaffWorkloadParameters`
directly with its own `Count`/`Max`. `get_editors_with_keywords` (`users.py:314`)
is a **real, live shared dependency**, not manual-only legacy code as
originally assumed — it's also called directly by three other test files
(`test_views.py`, `test_communications.py`, `test_logic.py`) unrelated to
`SupervisorAssignEditor`. Deleting it (as an earlier draft of this section
proposed) would be unnecessarily invasive and risk those unrelated tests.
Revised plan: **keep `get_editors_with_keywords` exactly as-is**, don't touch
`users.py` at all, and fix `_editors_with_keywords` by *intersecting* its
existing Account queryset with the same availability pipeline automatic
assignment uses, rather than rebuilding the candidate list from
`StaffWorkloadParameters` outward:

```python
from core.models import AccountRole, Role
from ..events.assignment import annotate_workload_availability, get_available_editor_parameters
from wjs.jcom_profile.constants import SECTION_EDITOR_ROLE

def _editors_with_keywords(self, search_text) -> QuerySet[Account]:
    article = self.object.article
    article_authors = article.author_accounts.all()
    try:
        current_editor = WjsEditorAssignment.objects.get_current(self.object).editor
    except WjsEditorAssignment.DoesNotExist:
        current_editor = None

    section_editors = AccountRole.objects.filter(
        journal=article.journal, role=Role.objects.get(slug=SECTION_EDITOR_ROLE),
    ).values_list("user")
    available_parameters = annotate_workload_availability(
        get_available_editor_parameters(journal=article.journal, editors=section_editors)
    )

    qs = (
        Account.objects.get_editors_with_keywords(article, current_editor)
        .filter(pk__in=available_parameters.values_list("user_id", flat=True))
        .exclude(pk__in=article_authors)
        .annotate(
            available_workload_month=Subquery(
                available_parameters.filter(user=OuterRef("pk")).values("available_workload_month")[:1],
            ),
            available_workload_30d=Subquery(
                available_parameters.filter(user=OuterRef("pk")).values("available_workload_30d")[:1],
            ),
        )
        .order_by(
            F("max_matching_keyword_weight").desc(nulls_last=True),
            "-num_shared_kwds",
            "-available_workload_30d",
            "id",
        )
    )
    if search_text:
        qs = qs.filter(Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text))
    return qs
```

This one change closes finding 1 (availability filter — the
`pk__in=available_parameters...` intersection; workload annotation — the
two `Subquery` fields; weight-based ordering — the `order_by`) **and**
finding 6 / D6 (role set) at once: `available_parameters` is built from the
same `SECTION_EDITOR_ROLE`-only `AccountRole` query
`default_assign_editors_to_articles` uses, so a plain `EDITOR_ROLE` account
returned by `get_editors_with_keywords`'s broader internal filter is
intersected away here without ever touching that function's own role list
(which stays `[EDITOR_ROLE, SECTION_EDITOR_ROLE]` for its other callers).
No `Case`/`When` order-preservation hack needed: `get_editors_with_keywords`
already annotates `num_shared_kwds`/`max_matching_keyword_weight` directly on
the `Account` queryset (that's what the template already reads — see
below), so the final `order_by` runs on those existing annotations plus the
two new `Subquery` ones, all on one queryset, no re-projection required.

**Test fixture fallout to check:** `test_eo_select_editor` and
`test_eo_select_new_editor` (`test_views.py`) currently exercise
`SupervisorAssignEditor` with the `section_editor`/`normal_user` fixtures
(`wjs/jcom_profile/tests/conftest.py:317`, `:347`), **neither of which
creates a `StaffWorkloadParameters` row**. Once `_editors_with_keywords`
requires `get_available_editor_parameters` availability (workload > 0), both
tests must give their target editor a `StaffWorkloadParameters` row with
`workload > 0` before submitting the form, or `selected_editor` will no
longer validate against the (now-filtered) queryset. See the plan's D5 task
for the exact fix.

**Template change:** `editor_table.html` gains an "Available this month"
column (`available_workload_month`), shown unconditionally — the wiki lists
it under "Values shown" without gating it behind the existing
`show_editor_keywords` per-journal flag that currently gates the keyword
columns. Sort order in the table follows `rank_editor_parameters`'s order,
matching "sorted according to the same rules as automatic assignment."

**Post-implementation correction (repo owner, after final review):** the
`get_available_editor_parameters` intersection above shipped initially as
written, then had to be reverted. The repo owner's ruling: manual selection
must match `wjs-develop`'s pre-existing behavior — every `SECTION_EDITOR_ROLE`
editor stays **visible and selectable** regardless of availability (disabled,
on vacation, over their monthly cap, or with no `StaffWorkloadParameters`
row configured at all); availability only *differentiates* (via the workload
column and the template's pre-existing "self disabled"/"unavailable" badges,
which had gone dead while the hard exclude was in place), it never excludes.
This applies **only** to the manual view — automatic assignment
(`default_assign_editors_to_articles`) keeps hard-excluding on availability,
unchanged. The shipped fix drops `get_available_editor_parameters` from
`_editors_with_keywords` entirely: `section_editors` (the `AccountRole`-based
role restriction, unchanged, still closes D6) now feeds a plain, unfiltered
`StaffWorkloadParameters.objects.filter(journal=..., user__in=section_editors)`
into `annotate_workload_availability`, and the final `order_by`'s workload
tie-break became `F("available_workload_30d").desc(nulls_last=True)` (not a
plain string) — an unconfigured editor now gets `NULL` there (no matching
`StaffWorkloadParameters` row for the `Subquery` to find), and Postgres sorts
NULLs first under plain `DESC`, which would otherwise rank an unconfigured
editor's *unknown* availability ahead of a configured editor's known value.

### D6 — Role set for manual candidates (finding 6) — resolved

**Decision (confirmed by repo owner): `SECTION_EDITOR_ROLE` only, same as
automatic assignment.** `EDITOR_ROLE` is dropped from the manual candidate
pool. This is a deliberate behavior change, not a preservation of today's
default: `Account.objects.get_editors_with_keywords`'s
`accountrole__role__slug__in=[constants.EDITOR_ROLE, constants.SECTION_EDITOR_ROLE]`
(`users.py:341`) currently lets a plain "Editor" show up as a manually
selectable candidate; after D5+D6 they won't. D5's rewrite builds its
candidate pool from `get_available_editor_parameters(journal=..., editors=...)`
fed by the same `AccountRole.objects.filter(role=Role.objects.get(slug=SECTION_EDITOR_ROLE))`
query `default_assign_editors_to_articles` already uses — one shared
role-filtered candidate query for both flows, no per-caller role parameter
needed.

## Function inventory (before → after)

| Today (`assignment.py`) | Becomes | Notes |
|---|---|---|
| `filter_parameters_by_matching_keywords` | `annotate_keyword_ranking` | D1: drops exclusion + trailing `order_by` |
| `annotate_automatic_assignment_workload` | `annotate_workload_availability` | D2: adds 30-day window, excludes WITHDRAWN, drops trailing `order_by`, drops built-in filter |
| *(none)* | `rank_editor_parameters` | D3: new, shared by both call sites |
| `default_assign_editors_to_articles` | same name | D4: pipeline reordered, fallback branch removed |
| `get_selected_editor_by_workload` | unchanged | still used by `jcom_assign_editors_to_articles` only |
| `jcom_assign_editors_to_articles` | unchanged | still keyword-blind, per bypass |
| `get_special_issue_parameters` / special-issue branch | unchanged | per bypass |
| `SupervisorAssignEditor._editors_with_keywords` (`views.py`) | rewritten (intersect + annotate, not rebuilt) | D5 |
| `Account.objects.get_editors_with_keywords` (`users.py`) | **unchanged** | still used internally by `annotate_keyword_ranking` and directly by three unrelated test files; D5 intersects its output rather than replacing it |
| `wjs_review/assign_editor/elements/editor_table.html` | gains a workload column | D5 |

## Wiki staleness — resolved

Rev 1.1.3 rewrote "Implementation details → Keywords matching" (prose and
sample code) to drop the hard filter and rename the function to
`annotate_keyword_ranking`, and fixed the "Manual editor selection view"
bullet that still read "filtered / sorted by keyword". The wiki's sample
code is now byte-for-byte the same shape as D1's `annotate_keyword_ranking`
above (no `.filter(keywords__in=...)`, no trailing `.order_by(...)`, same
function name). No further wiki inconsistency to track — D1–D6 above are
implementing what the wiki now says, not deviating from it.

## Validation plan

- Rewrite the existing keyword/workload tests in
  `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` added
  by the 2026-09-17 plan: the "excludes no match" test (D1) inverts to
  "keeps, ranked last"; the fallback test (D4) is replaced by a
  "no keyword match still picks the best-workload candidate through the
  same ranked queryset, no separate fallback call" test; add a withdrawn-article
  regression test for D2 (an editor with only a withdrawn paper this month
  keeps full `available_workload_month`); add a 30-day-vs-calendar-month
  divergence test for D3 (an assignment 20 days old but from *last* calendar
  month should count against `available_workload_30d` but not
  `available_workload_month`, or vice versa depending on where in the month
  it lands — pick a fixed date via `freezer`/`pytest-freezer` to make this
  deterministic).
- New tests for D5 in `wjs/plugins/wjs_review/tests/test_views.py` (or
  wherever `SupervisorAssignEditor` is currently tested): disabled/on-vacation
  editors no longer appear; ordering matches `rank_editor_parameters`;
  workload column value matches `available_workload_month`.
- Explicit NULL-ordering regression test for the D3 pitfall: an editor with
  no keyword match but the best workload must still rank *after* any editor
  with any keyword match at all, not before.
- Full `wjs_review` + `wjs.jcom_profile` suites per `.claude/rules/tests.md`
  (targeted files first, full plugin suite before merging).

## Follow-up

This document is a design, not a task-by-task plan. Before implementation,
run this through `superpowers:writing-plans` (or `/make-plan`) to produce
the TDD-structured plan `docs/superpowers/plans/` expects, mirroring the
2026-09-17 plan's format — including explicit "confirmed by repo owner"
sign-off on D6 (role set) before it's coded.
