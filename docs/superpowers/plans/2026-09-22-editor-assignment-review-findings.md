# Editor Assignment — Review Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile `wjs/plugins/wjs_review/events/assignment.py` (automatic editor assignment) and `SupervisorAssignEditor._editors_with_keywords` (manual editor assignment, `views.py`) with the "Editor Selection logic" wiki (rev 1.1.3): keyword matching never excludes a candidate (only ranks), the workload cap excludes withdrawn papers, filtering and ranking use two distinct workload windows (calendar-month cap vs. trailing-30-day rank), and manual assignment reuses the exact same availability/ranking pipeline as automatic assignment, restricted to `SECTION_EDITOR_ROLE`.

**Architecture:** Three small `StaffWorkloadParameters`-scoped functions in `assignment.py` — `annotate_keyword_ranking` (rank-only, delegates keyword counting to the existing `Account.objects.get_editors_with_keywords`), `annotate_workload_availability` (annotates both the calendar-month and trailing-30-day workload windows, excluding withdrawn papers, never filters), `rank_editor_parameters` (the one shared final ordering, `NULLS LAST` on keyword weight). `default_assign_editors_to_articles` chains all three and applies the monthly-cap exclusion itself; the old "no keyword match → fallback to a different workload metric" branch is removed, since nothing is excluded by keyword matching anymore. `SupervisorAssignEditor._editors_with_keywords` (manual "Select new Editor" view) is fixed by *intersecting* its existing `Account`-scoped queryset (unchanged, from `get_editors_with_keywords`) with the same availability pipeline, rather than being rebuilt from scratch.

**Tech Stack:** Django ORM (`QuerySet.annotate`/`Subquery`/`Coalesce`/`Case`/`When`/`Least`/`F(...).desc(nulls_last=True)`), pytest + pytest-django, `freezegun` for date-deterministic tests, existing `wjs_review`/`wjs.jcom_profile` fixtures.

**Spec:** `docs/superpowers/specs/2026-09-22-editor-assignment-review-findings-design.md` (sections D1–D6). That design document itself supersedes the keyword-filtering behavior built by `docs/superpowers/plans/2026-09-17-automatic-assignment-keyword-matching.md`; this plan implements it.

## Global Constraints

- `Account.objects.get_editors_with_keywords` (`wjs/plugins/wjs_review/users.py`) stays **completely unchanged** — do not touch `users.py`. It is the one shared implementation of "how do we count/weight matching keywords," used both by the new `annotate_keyword_ranking` and directly by three test files unrelated to this plan (`test_views.py`, `test_communications.py`, `test_logic.py`).
- `get_selected_editor_by_workload`, `get_available_editor_parameters`, `get_special_issue_parameters` stay unchanged, reused as-is.
- The special-issue branch and `jcom_assign_editors_to_articles` (JCOM/JCOMAL director bypass) remain keyword-blind — do not add keyword matching to either.
- Manual assignment's candidate pool is `SECTION_EDITOR_ROLE` only (D6, confirmed by repo owner) — a plain `EDITOR_ROLE` holder must not be manually selectable, even though `get_editors_with_keywords` itself keeps considering both roles for its own other callers (its role filter is not touched).
- Every renamed function/attribute (`filter_parameters_by_matching_keywords`→`annotate_keyword_ranking`; `annotate_automatic_assignment_workload`→`annotate_workload_availability`; `automatic_available_workload`→`available_workload_month`; `assignment_count`→`assignment_count_month`) must be updated at **every** call site, including tests. Grep for the old name before finishing the task that removes it.
- Every new/changed test must run green with:
  `PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest <path> -q`
  (this is the interpreter with `wjs-submission`/`wjs.jcom_profile` installed in editable mode from these exact repo checkouts).

---

## File Structure

- **Modify:** `wjs/plugins/wjs_review/events/assignment.py` — the three annotate/rank functions and `default_assign_editors_to_articles`.
- **Modify:** `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` — renamed/rewritten unit tests, new withdrawn/30-day/NULL-ordering tests.
- **Modify:** `wjs/plugins/wjs_review/views.py` — `SupervisorAssignEditor._editors_with_keywords`.
- **Modify:** `wjs/plugins/wjs_review/tests/test_views.py` — fixture fixes for two existing `SupervisorAssignEditor` tests, new availability/role tests.
- **Modify:** `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html` — new "Available this month" column header.
- **Modify:** `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html` — new column cell.

No new files. No migration: no model fields change, only queryset annotations.

---

### Task 1: `annotate_keyword_ranking` (added alongside the old function)

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Modify: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `Account.objects.get_editors_with_keywords(article, current_editor=None, include_matching_keywords=True)` (`wjs/plugins/wjs_review/users.py:314`, unchanged), `StaffWorkloadParameters` (`wjs/jcom_profile/models.py:196`).
- Produces: `annotate_keyword_ranking(article: Article, users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]`, annotated with `matching_keywords_count: int` (always ≥ 0, never `None`) and `max_matching_keyword_weight: int | None` (`None` means no match). **Never excludes a row.** Used by Task 3 (`rank_editor_parameters`) and Task 4.

Old `filter_parameters_by_matching_keywords` is left in place and still used by `default_assign_editors_to_articles` in this task — it is only removed in Task 4, once the pipeline is fully rewired. This task only adds the new function alongside it, so the whole test file stays green throughout.

- [ ] **Step 1: Write the failing tests**

Add `annotate_keyword_ranking` to the existing `from ..events.assignment import (...)` block in `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` (do not remove `filter_parameters_by_matching_keywords` yet):

```python
from ..events.assignment import (
    annotate_automatic_assignment_workload,
    annotate_keyword_ranking,
    filter_parameters_by_matching_keywords,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
)
```

Append these tests at the end of the file:

```python
@pytest.mark.django_db
def test_annotate_keyword_ranking_keeps_no_match_editors(article, editors, keywords):
    """Editors with no keyword overlap with the article are annotated, not excluded."""
    article.keywords.add(keywords[0])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)

    result = annotate_keyword_ranking(article, parameters)

    assert result.count() == 3
    for parameter in result:
        assert parameter.matching_keywords_count == 0
        assert parameter.max_matching_keyword_weight is None


@pytest.mark.django_db
def test_annotate_keyword_ranking_annotates_weight_and_count(article, editors, keywords):
    """Each parameter is annotated with its own matching-keyword count and max weight."""
    article.keywords.add(keywords[0], keywords[1], keywords[2])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    param_two_matches, param_one_match, param_no_match = parameters.order_by("id")

    StaffKeyword.objects.create(parameters=param_two_matches, keyword=keywords[0], weight=1)
    StaffKeyword.objects.create(parameters=param_two_matches, keyword=keywords[1], weight=1)
    StaffKeyword.objects.create(parameters=param_one_match, keyword=keywords[0], weight=5)
    # param_no_match's keyword isn't one of the article's, so it must still be annotated at
    # zero/None, not excluded.
    StaffKeyword.objects.create(parameters=param_no_match, keyword=keywords[9], weight=99)

    result = {p.pk: p for p in annotate_keyword_ranking(article, parameters)}

    assert result[param_two_matches.pk].matching_keywords_count == 2
    assert result[param_two_matches.pk].max_matching_keyword_weight == 1
    assert result[param_one_match.pk].matching_keywords_count == 1
    assert result[param_one_match.pk].max_matching_keyword_weight == 5
    assert result[param_no_match.pk].matching_keywords_count == 0
    assert result[param_no_match.pk].max_matching_keyword_weight is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_annotate_keyword_ranking" -q
```
Expected: `ImportError: cannot import name 'annotate_keyword_ranking'`.

- [ ] **Step 3: Implement the function**

In `wjs/plugins/wjs_review/events/assignment.py`, add the new function directly after `filter_parameters_by_matching_keywords` (do not remove that function yet):

```python
def annotate_keyword_ranking(
    article: Article,
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Annotate StaffWorkloadParameters with how well they match the article's keywords. Never excludes.

    Delegates the actual keyword-matching computation to
    :py:meth:`Account.objects.get_editors_with_keywords` (the same annotation used for manual
    editor selection in the "Select new editor" page), skipping its per-editor matching-keywords
    list (not needed here), so only one shared implementation of "how do we count/weight matching
    keywords" exists. Editors with no matching keyword are kept, annotated with
    ``matching_keywords_count=0``/``max_matching_keyword_weight=None`` — ranking them last is the
    caller's job (see :py:func:`rank_editor_parameters`), not this function's.

    :param article: The article whose keywords are matched against.
    :type article: Article
    :param users_parameters: The candidate StaffWorkloadParameters to annotate.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: ``users_parameters`` annotated with ``matching_keywords_count``/
        ``max_matching_keyword_weight`` (sourced from ``get_editors_with_keywords``), unfiltered.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    annotated_accounts = Account.objects.filter(
        pk__in=users_parameters.values_list("user_id", flat=True),
    ).get_editors_with_keywords(article, include_matching_keywords=False)
    return users_parameters.annotate(
        matching_keywords_count=Coalesce(
            Subquery(annotated_accounts.filter(pk=OuterRef("user_id")).values("num_shared_kwds")[:1]),
            0,
        ),
        max_matching_keyword_weight=Subquery(
            annotated_accounts.filter(pk=OuterRef("user_id")).values("max_matching_keyword_weight")[:1],
        ),
    )
```

`Coalesce` is already imported (`from django.db.models.functions import Cast, Coalesce, Least, NullIf`) — no new imports needed for this step.

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 2 passed.

- [ ] **Step 5: Run the full file to confirm no regression**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -q
```
Expected: all pre-existing tests still pass (this task only *adds* a function; `default_assign_editors_to_articles` still calls the old `filter_parameters_by_matching_keywords`, untouched).

- [ ] **Step 6: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat: add annotate_keyword_ranking (keyword matching never excludes, only ranks)"
```

---

### Task 2: `annotate_workload_availability` (added alongside the old function)

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Modify: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `WjsEditorAssignment` (`wjs/plugins/wjs_review/models.py:1785`), `ArticleWorkflow.ReviewStates.WITHDRAWN` (`wjs/plugins/wjs_review/models.py:400`, value `"Withdrawn"`), `StaffWorkloadParameters.workload`/`.brake_on`.
- Produces: `annotate_workload_availability(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]`, annotated with `assignment_count_month`, `assignment_count_30d`, `effective_workload_cap`, `available_workload_month`, `available_workload_30d`. **Never filters, never orders** — both are the caller's job (Task 4, Task 5). Both counts exclude articles whose `articleworkflow.state == WITHDRAWN`. Used by Task 3 and Task 4.

Old `annotate_automatic_assignment_workload` is left in place in this task (still used by `default_assign_editors_to_articles`); removed in Task 4.

- [ ] **Step 1: Write the failing tests**

`from datetime import timedelta` is already imported at the top of the test file (line 4) — no change needed there. Add `import freezegun` alongside the existing `import random` (line 3):

```python
import random
from datetime import timedelta
from typing import Callable

import freezegun
import pytest
```

```python
from ..events.assignment import (
    annotate_automatic_assignment_workload,
    annotate_keyword_ranking,
    annotate_workload_availability,
    filter_parameters_by_matching_keywords,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
)
```

Append these tests:

```python
@pytest.mark.django_db
def test_annotate_workload_availability_uses_smaller_of_workload_and_brake_on(article, editors, journal):
    """When brake_on is set (> 0), the effective cap is min(workload, brake_on), not workload alone."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = 3
    parameters.save()

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.effective_workload_cap == 3
    assert result.available_workload_month == 3
    assert result.available_workload_30d == 3


@pytest.mark.django_db
def test_annotate_workload_availability_ignores_zero_brake_on(article, editors, journal):
    """A brake_on of 0 (the model default) means "no EO override": fall back to plain workload."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = 0
    parameters.save()

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.effective_workload_cap == 10
    assert result.available_workload_month == 10
    assert result.available_workload_30d == 10


@pytest.mark.django_db
def test_annotate_workload_availability_counts_only_current_month(article, editors, journal, fake_request):
    """Only WjsEditorAssignment rows assigned in the current calendar month count against the monthly cap."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    last_month_article = Article.objects.create(
        journal=journal,
        title="Old article",
        owner=editors[0],
        correspondence_author=editors[0],
    )
    old_assignment = WjsEditorAssignment.objects.create(
        article=last_month_article,
        editor=editors[0],
        editor_type="section-editor",
    )
    WjsEditorAssignment.objects.filter(pk=old_assignment.pk).update(
        assigned=timezone.now() - timedelta(days=45),
    )

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1


@pytest.mark.django_db
def test_annotate_workload_availability_scoped_to_journal(
    article,
    editors,
    journal,
    journal_factory,
):
    """
    The monthly assignment count must be scoped to each StaffWorkloadParameters' own journal.

    An editor who also works for another journal (e.g. JCOM and JCOMAL) must not have an assignment
    made in that other journal count against their capacity here: `StaffWorkloadParameters` is unique
    per (user, journal), so the subquery must correlate on the outer row's own journal, not just the
    editor.
    """
    other_journal = journal_factory("JOTHER")
    editor = editors[0]

    parameters = StaffWorkloadParameters.objects.get(user=editor, journal=journal)
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    StaffWorkloadParameters.objects.create(user=editor, journal=other_journal, workload=1)

    other_journal_article = Article.objects.create(
        journal=other_journal,
        title="Other journal article",
        owner=editor,
        correspondence_author=editor,
    )
    WjsEditorAssignment.objects.create(
        article=other_journal_article,
        editor=editor,
        editor_type="section-editor",
    )

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1


@pytest.mark.django_db
def test_annotate_workload_availability_does_not_exclude_exhausted_editors(article, editors, journal):
    """
    An editor with no availability left this month is annotated, not filtered out.

    Filtering on available_workload_month is now the caller's job (default_assign_editors_to_articles),
    not this function's — the manual selection view deliberately does NOT filter on it (EO can
    override the cap), so the annotation function itself must stay non-filtering.
    """
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    WjsEditorAssignment.objects.create(article=article, editor=editors[0], editor_type="section-editor")

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.available_workload_month == 0


@pytest.mark.django_db
def test_annotate_workload_availability_excludes_withdrawn_papers(article, editors, journal):
    """A withdrawn paper does not count against either workload window."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    withdrawn_article = Article.objects.create(
        journal=journal,
        title="Withdrawn article",
        owner=editors[0],
        correspondence_author=editors[0],
    )
    withdrawn_article.articleworkflow.state = ArticleWorkflow.ReviewStates.WITHDRAWN
    withdrawn_article.articleworkflow.save()
    WjsEditorAssignment.objects.create(
        article=withdrawn_article,
        editor=editors[0],
        editor_type="section-editor",
    )

    result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1
    assert result.assignment_count_30d == 0
    assert result.available_workload_30d == 1


@pytest.mark.django_db
def test_annotate_workload_availability_30d_and_month_windows_diverge(article, editors, journal):
    """
    An assignment from 25 days ago, but before the start of the current calendar month, counts
    against the 30-day window but not the calendar-month window — proving the two windows are
    genuinely independent, not the same count read twice.

    Frozen at a fixed date (2026-01-20) so the scenario doesn't depend on which day of the month
    the test suite happens to run on.
    """
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    with freezegun.freeze_time("2026-01-20T12:00:00+00:00"):
        other_article = Article.objects.create(
            journal=journal,
            title="Other article",
            owner=editors[0],
            correspondence_author=editors[0],
        )
        old_assignment = WjsEditorAssignment.objects.create(
            article=other_article,
            editor=editors[0],
            editor_type="section-editor",
        )
        WjsEditorAssignment.objects.filter(pk=old_assignment.pk).update(
            assigned=timezone.now() - timedelta(days=25),
        )

        result = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert result.assignment_count_month == 0
    assert result.available_workload_month == 1
    assert result.assignment_count_30d == 1
    assert result.available_workload_30d == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_annotate_workload_availability" -q
```
Expected: `ImportError: cannot import name 'annotate_workload_availability'`.

- [ ] **Step 3: Implement the function**

Add `Q` to the `django.db.models` import line in `assignment.py` (needed for the `not_withdrawn` exclusion) — the current line is:
```python
from django.db.models import (
    Case,
    F,
    FloatField,
    Func,
    OuterRef,
    QuerySet,
    Subquery,
    When,
)
```
becomes:
```python
from django.db.models import (
    Case,
    F,
    FloatField,
    Func,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    When,
)
```

Add the new function directly after `annotate_automatic_assignment_workload` (do not remove that function yet):

```python
def annotate_workload_availability(
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Annotate parameters with monthly-cap and trailing-30-day workload availability. Never filters.

    The effective monthly cap is the smaller of ``workload`` and ``brake_on``. ``brake_on``
    defaults to 0, which means "no EO override" (a 0 ``brake_on`` is ignored and the cap falls
    back to plain ``workload``; only ``brake_on > 0`` participates in the ``min()``).

    Two independent windows are annotated, per the "Editor Selection logic" spec's "Workload"
    section: ``available_workload_month`` (current calendar month, used by automatic assignment's
    own *filtering* exclusion) and ``available_workload_30d`` (trailing 30 days, used purely as
    the *ranking* tie-break by both automatic and manual assignment). Both counts exclude
    WITHDRAWN articles. Deselected assignments need no explicit exclusion: BaseDeassignEditor
    (`logic.py`) deletes the WjsEditorAssignment row outright on deselection, so it is already
    gone from either count by construction.

    Never filters or orders the queryset — both are the caller's decision (automatic assignment
    filters on ``available_workload_month__gt=0``; the manual selection view deliberately does
    not, so an EO can see and pick an over-cap editor).

    :param users_parameters: The candidate StaffWorkloadParameters to annotate.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: ``users_parameters`` annotated with ``assignment_count_month``, ``assignment_count_30d``,
        ``effective_workload_cap``, ``available_workload_month`` and ``available_workload_30d``.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    from ..models import ArticleWorkflow, WjsEditorAssignment

    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    window_30d = timezone.localtime() - datetime.timedelta(days=30)
    not_withdrawn = ~Q(article__articleworkflow__state=ArticleWorkflow.ReviewStates.WITHDRAWN)

    assigned_this_month = (
        WjsEditorAssignment.objects.filter(
            not_withdrawn,
            editor=OuterRef("user_id"),
            article__journal=OuterRef("journal_id"),
            assigned__gte=month_start,
        )
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    assigned_last_30d = (
        WjsEditorAssignment.objects.filter(
            not_withdrawn,
            editor=OuterRef("user_id"),
            article__journal=OuterRef("journal_id"),
            assigned__gte=window_30d,
        )
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    return users_parameters.annotate(
        assignment_count_month=Coalesce(Subquery(assigned_this_month), 0),
        assignment_count_30d=Coalesce(Subquery(assigned_last_30d), 0),
        effective_workload_cap=Case(
            When(brake_on__gt=0, then=Least(F("workload"), F("brake_on"))),
            default=F("workload"),
        ),
        available_workload_month=F("effective_workload_cap") - F("assignment_count_month"),
        available_workload_30d=F("effective_workload_cap") - F("assignment_count_30d"),
    )
```

`assignment.py` has no `datetime` import at all today. Add `import datetime` as the first import line in the file (before `from typing import TYPE_CHECKING, Optional`), so `datetime.timedelta(days=30)` (used above) resolves. `timezone` is already imported (`from django.utils import timezone`).

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 7 passed.

- [ ] **Step 5: Run the full file to confirm no regression**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -q
```
Expected: all pass (this task only adds a function; the pipeline still uses the old one).

- [ ] **Step 6: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat: add annotate_workload_availability with monthly and 30-day windows, excluding withdrawn papers"
```

---

### Task 3: `rank_editor_parameters`

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Modify: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: the output of `annotate_keyword_ranking` + `annotate_workload_availability` chained (Tasks 1–2): `matching_keywords_count`, `max_matching_keyword_weight`, `available_workload_30d`.
- Produces: `rank_editor_parameters(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]`, ordered by keyword weight (NULLs last) → keyword count → 30-day workload → id. Used by Task 4 and Task 5.

- [ ] **Step 1: Write the failing tests**

Add `rank_editor_parameters` to the import block:

```python
from ..events.assignment import (
    annotate_automatic_assignment_workload,
    annotate_keyword_ranking,
    annotate_workload_availability,
    filter_parameters_by_matching_keywords,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
    rank_editor_parameters,
)
```

Append these tests:

```python
@pytest.mark.django_db
def test_rank_editor_parameters_orders_by_weight_then_count_then_workload(article, editors, keywords):
    """Final order: highest keyword weight, then highest keyword count, then highest 30-day workload, then id."""
    article.keywords.add(keywords[0], keywords[1], keywords[2])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    for parameter in parameters:
        parameter.workload = 10
        parameter.save()
    param_low_weight_two_matches, param_high_weight_one_match, param_no_match = parameters.order_by("id")

    StaffKeyword.objects.create(parameters=param_low_weight_two_matches, keyword=keywords[0], weight=1)
    StaffKeyword.objects.create(parameters=param_low_weight_two_matches, keyword=keywords[1], weight=1)
    StaffKeyword.objects.create(parameters=param_high_weight_one_match, keyword=keywords[0], weight=5)

    ranked = rank_editor_parameters(
        annotate_workload_availability(annotate_keyword_ranking(article, parameters)),
    )

    assert list(ranked) == [param_high_weight_one_match, param_low_weight_two_matches, param_no_match]


@pytest.mark.django_db
def test_rank_editor_parameters_puts_no_keyword_match_last_not_first(article, editors, keywords):
    """
    Regression guard: a NULL max_matching_keyword_weight (no keyword match at all) must sort
    LAST, not first. Postgres sorts NULLs FIRST under a plain "-max_matching_keyword_weight"
    DESC order_by — rank_editor_parameters must use nulls_last=True explicitly, or a
    non-matching editor would rank ahead of any editor with a real match.
    """
    article.keywords.add(keywords[0])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    for parameter in parameters:
        parameter.workload = 10
        parameter.save()
    param_no_match, param_low_weight_match, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=param_low_weight_match, keyword=keywords[0], weight=1)

    ranked = list(
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters))),
    )

    assert ranked[0] == param_low_weight_match
    assert ranked[0].max_matching_keyword_weight == 1
    assert param_no_match in ranked[1:]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_rank_editor_parameters" -q
```
Expected: `ImportError: cannot import name 'rank_editor_parameters'`.

- [ ] **Step 3: Implement the function**

Add directly after `annotate_workload_availability`:

```python
def rank_editor_parameters(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]:
    """
    Apply the canonical final ordering: keyword weight, then keyword count, then 30-day workload, then id.

    Uses ``F("max_matching_keyword_weight").desc(nulls_last=True)`` rather than a plain
    ``"-max_matching_keyword_weight"`` string: Postgres sorts NULLs FIRST under DESC by default,
    which would rank a non-matching editor ahead of any editor with a real (if low) match —
    exactly backwards for this ranking.

    :param users_parameters: A queryset already annotated by both annotate_keyword_ranking and
        annotate_workload_availability.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The same queryset, ordered best-candidate-first.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    return users_parameters.order_by(
        F("max_matching_keyword_weight").desc(nulls_last=True),
        "-matching_keywords_count",
        "-available_workload_30d",
        "id",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 2 passed.

- [ ] **Step 5: Run the full file to confirm no regression**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat: add rank_editor_parameters, the shared final ordering for editor candidates"
```

---

### Task 4: Rewire `default_assign_editors_to_articles`; remove the old functions and the fallback branch

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Modify: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `annotate_keyword_ranking`, `annotate_workload_availability`, `rank_editor_parameters` (Tasks 1–3); existing `get_available_editor_parameters`, `get_special_issue_parameters`, `get_selected_editor_by_workload` (all unchanged), `BaseAssignToEditor` (`logic.py`).
- Produces: `default_assign_editors_to_articles(article, **kwargs) -> Optional["WjsEditorAssignment"]` — same public signature, now with no separate fallback path.

This is the task where old names are fully retired: `filter_parameters_by_matching_keywords` and `annotate_automatic_assignment_workload` are deleted, along with the tests that exercised only their old (excluding/filtering) behavior. Grep for both names at the end of this task to confirm nothing references them anymore.

- [ ] **Step 1: Write the failing test**

Replace `test_default_assign_editors_falls_back_to_workload_when_no_keyword_match` in `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` with:

```python
@pytest.mark.django_db
def test_default_assign_editors_picks_best_workload_when_no_keyword_match(
    review_settings,
    admin,
    article,
    editors,
    coauthors_setting,
):
    """
    With no keyword overlap for any candidate, all candidates tie on keyword ranking (0 matches,
    no weight) and selection falls through to the workload tie-break within the same ranked
    queryset -- there is no separate "fallback" code path or metric anymore.

    Goes through the real HTTP submission pipeline (like the other tests in this file), rather
    than calling default_assign_editors_to_articles directly, so the automatic assignment runs
    with the request context that get_current_request() needs.
    """
    article.primary_issue = None
    article.save()
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    expected_editor = (
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters)))
        .filter(available_workload_month__gt=0)
        .first()
        .user
    )

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    editor_assignment = WjsEditorAssignment.objects.get(article=article)
    assert editor_assignment.editor == expected_editor
```

Remove these three now-superseded tests entirely (they test the old, hard-filtering `filter_parameters_by_matching_keywords` and the old, single-window `annotate_automatic_assignment_workload` that this task deletes):

- `test_filter_parameters_by_matching_keywords_excludes_no_match`
- `test_filter_parameters_by_matching_keywords_orders_by_weight_then_count`
- `test_annotate_automatic_assignment_workload_uses_smaller_of_workload_and_brake_on`
- `test_annotate_automatic_assignment_workload_ignores_zero_brake_on`
- `test_annotate_automatic_assignment_workload_counts_only_current_month`
- `test_annotate_automatic_assignment_workload_scoped_to_journal`
- `test_annotate_automatic_assignment_workload_excludes_exhausted_editors`

(Their replacements, `test_annotate_keyword_ranking_*` and `test_annotate_workload_availability_*`, already exist from Tasks 1–2.)

Update the import block to drop the two retired names (this test file never imports
`default_assign_editors_to_articles`/`jcom_assign_editors_to_articles` directly — every test
in it goes through the real HTTP submission pipeline via `override_settings` and the
`DEFAULT_ASSIGN_EDITORS_TO_ARTICLES`/`JCOM_ASSIGN_EDITORS_TO_ARTICLES` dotted-path string
constants already defined near the top of the file — so do not add either name here, only
remove the two retired ones):

```python
from ..events.assignment import (
    annotate_keyword_ranking,
    annotate_workload_availability,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
    rank_editor_parameters,
)
```

- [ ] **Step 2: Run the new test to verify it fails, and confirm the removed tests are gone**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_default_assign_editors_picks_best_workload_when_no_keyword_match" -q
```
Expected: FAILS — the current pipeline still uses the old fallback (`get_selected_editor_by_workload`'s open-states metric), which picks a possibly-different editor than the new calendar-month/30-day-based ranking this test expects.

- [ ] **Step 3: Rewire `default_assign_editors_to_articles` and delete the two old functions**

In `wjs/plugins/wjs_review/events/assignment.py`, delete `filter_parameters_by_matching_keywords` and `annotate_automatic_assignment_workload` entirely (their replacements from Tasks 1–2 are already in the file). Replace `default_assign_editors_to_articles` with:

```python
def default_assign_editors_to_articles(article: Article, **kwargs) -> Optional["WjsEditorAssignment"]:
    """
    Assign editors to article for review. Default algorithm.

    Keyword matching never excludes a candidate: an editor with no matching keyword is kept in
    the ranked queryset, ranked last (behind any editor with a real match), per the "Editor
    Selection logic" spec (rev 1.1.3). The only exclusions in the non-special-issue branch are
    availability (get_available_editor_parameters), authorship, and running out of monthly
    workload (available_workload_month__gt=0) — there is no separate "no keyword match" fallback
    path or metric anymore.
    """
    from ..logic import BaseAssignToEditor

    is_special_issue = bool(article.primary_issue and article.primary_issue.managing_editors.exists())
    if is_special_issue:
        # Special issues keep their own dedicated editor pool and pure-workload selection;
        # keyword matching is not applied here (see spec's "bypasses" section:
        # https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic#implementation-details).
        parameters = get_special_issue_parameters(article)
    else:
        editors = AccountRole.objects.filter(
            journal=article.journal,
            role=Role.objects.get(slug=SECTION_EDITOR_ROLE),
        ).values_list("user")
        parameters = get_available_editor_parameters(journal=article.journal, editors=editors)
    parameters = parameters.exclude(user__in=article.author_accounts.all())
    if parameters:
        request = get_current_request()
        if is_special_issue:
            user = get_selected_editor_by_workload(parameters, journal=article.journal)
        else:
            ranked_parameters = rank_editor_parameters(
                annotate_workload_availability(annotate_keyword_ranking(article, parameters)).filter(
                    available_workload_month__gt=0,
                ),
            )
            matched_parameter = ranked_parameters.first()
            user = matched_parameter.user if matched_parameter else None
        if user:
            assignment = BaseAssignToEditor(editor=user, article=article, request=request, first_assignment=True).run()
            return assignment
```

- [ ] **Step 4: Run the new test to verify it passes**

Run the same command as Step 2. Expected: 1 passed.

- [ ] **Step 5: Run the full file to confirm no regression**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -q
```
Expected: all pass. In particular:
- `test_default_assign_editors_prefers_keyword_match_over_workload` (Task 1's era, unrelated to this task's changes) still picks the keyword-matched editor.
- `test_jcom_assign_editors_ignores_keywords`, `test_default_special_issue_assignment_ignores_keywords` still pass unchanged (neither branch touched).
- `test_workload_decrease_editor` still passes: with no keywords set on the article at all, all candidates tie at `(0, None)` on keyword ranking in every parametrization, so the assertions (which only check "a different editor got picked the second time," never the exact identity of the third pick) hold under the new workload metric exactly as they did under the old one.

- [ ] **Step 6: Grep to confirm the old names are fully gone**

```bash
grep -rn "filter_parameters_by_matching_keywords\|annotate_automatic_assignment_workload\|automatic_available_workload" wjs/plugins/wjs_review/ wjs/jcom_profile/
```
Expected: no matches.

- [ ] **Step 7: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "refactor: rewire default_assign_editors_to_articles onto the non-excluding keyword/workload pipeline, drop the fallback branch"
```

---

### Task 5: Manual selection view (`SupervisorAssignEditor._editors_with_keywords`)

**Files:**
- Modify: `wjs/plugins/wjs_review/views.py`
- Modify: `wjs/plugins/wjs_review/tests/test_views.py`

**Interfaces:**
- Consumes: `Account.objects.get_editors_with_keywords` (unchanged, `users.py`), `annotate_workload_availability`, `get_available_editor_parameters` (Task 2 / existing, both from `..events.assignment`), `core.models.AccountRole`/`Role`, `wjs.jcom_profile.constants.SECTION_EDITOR_ROLE`.
- Produces: `SupervisorAssignEditor._editors_with_keywords(search_text) -> QuerySet[Account]`, now filtered to available `SECTION_EDITOR_ROLE` holders and annotated with `available_workload_month`/`available_workload_30d`, ordered by the same rule as `rank_editor_parameters`.

**Fixture fallout to fix first:** `test_eo_select_editor` and `test_eo_select_new_editor` (`test_views.py`) use the `section_editor`/`normal_user` fixtures (`wjs/jcom_profile/tests/conftest.py:317`, `:347`), neither of which creates a `StaffWorkloadParameters` row. Once `_editors_with_keywords` requires `get_available_editor_parameters` availability (`workload > 0`), both target editors need one, or `selected_editor` will no longer validate against the (now-filtered) form queryset.

- [ ] **Step 1: Write the failing tests**

In `wjs/plugins/wjs_review/tests/test_views.py`, add `from wjs.jcom_profile import constants` to the imports (near the existing `from wjs.jcom_profile.models import ...` line):

```python
from wjs.jcom_profile import constants
from wjs.jcom_profile.models import JCOMProfile, StaffKeyword, StaffWorkloadParameters
```

Fix `test_eo_select_editor` — add a `StaffWorkloadParameters` row for `section_editor` right before `view = SupervisorAssignEditor()`:

```python
    fake_request.POST = {
        "selected_editor": section_editor.janeway_account.pk,
        "note_for_past_editor": "test",
        "note_for_new_editor": "test",
    }
    StaffWorkloadParameters.objects.create(
        user=section_editor.janeway_account, journal=submitted_workflow.article.journal, workload=10,
    )
    view = SupervisorAssignEditor()
```

Fix `test_eo_select_new_editor` — add a `StaffWorkloadParameters` row for `normal_user` right after its role is granted:

```python
    normal_user.janeway_account.add_account_role("section-editor", assigned_article.journal)
    StaffWorkloadParameters.objects.create(
        user=normal_user.janeway_account, journal=assigned_article.journal, workload=10,
    )
    old_editor = WjsEditorAssignment.objects.get_current(assigned_article).editor
```

Append these new tests after `test_eo_select_new_editor`:

```python
@pytest.mark.django_db
def test_supervisor_assign_editor_excludes_unavailable_and_non_section_editor(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    review_settings,
):
    """
    _editors_with_keywords only returns SECTION_EDITOR_ROLE holders with monthly workload left,
    enabled and not on vacation — the same availability pipeline automatic assignment uses
    (per the "Editor Selection logic" spec's D5/D6).
    """
    journal = submitted_workflow.article.journal
    StaffWorkloadParameters.objects.create(user=section_editor.janeway_account, journal=journal, workload=10)

    disabled_editor = create_jcom_user("disabled_section_editor")
    disabled_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(
        user=disabled_editor.janeway_account, journal=journal, workload=10, enabled=False,
    )

    plain_editor_role_user = create_jcom_user("plain_editor_role")
    plain_editor_role_user.add_account_role(constants.EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(user=plain_editor_role_user.janeway_account, journal=journal, workload=10)

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = view._editors_with_keywords(search_text=None)

    assert section_editor.janeway_account in candidates
    assert disabled_editor.janeway_account not in candidates
    assert plain_editor_role_user.janeway_account not in candidates


@pytest.mark.django_db
def test_supervisor_assign_editor_annotates_and_orders_by_workload(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    review_settings,
):
    """
    _editors_with_keywords annotates available_workload_month and orders candidates with no
    keyword match (both editors here have none) by descending 30-day workload availability.
    """
    journal = submitted_workflow.article.journal
    StaffWorkloadParameters.objects.create(
        user=section_editor.janeway_account, journal=journal, workload=1,
    )

    high_availability_editor = create_jcom_user("high_availability_editor")
    high_availability_editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
    StaffWorkloadParameters.objects.create(
        user=high_availability_editor.janeway_account, journal=journal, workload=10,
    )

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = list(view._editors_with_keywords(search_text=None))

    section_editor_candidate = next(c for c in candidates if c.pk == section_editor.janeway_account.pk)
    high_availability_candidate = next(c for c in candidates if c.pk == high_availability_editor.janeway_account.pk)
    assert section_editor_candidate.available_workload_month == 1
    assert high_availability_candidate.available_workload_month == 10
    assert candidates.index(high_availability_candidate) < candidates.index(section_editor_candidate)
```

- [ ] **Step 2: Run tests to verify current behavior**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_views.py -k "test_eo_select_editor or test_eo_select_new_editor or test_supervisor_assign_editor" -q
```
Expected: `test_eo_select_editor`/`test_eo_select_new_editor` still PASS (the `StaffWorkloadParameters` addition is harmless against today's code, which doesn't check availability yet). The two new tests FAIL: `test_supervisor_assign_editor_excludes_unavailable_and_non_section_editor` fails because today's `_editors_with_keywords` returns the disabled editor and the plain-`EDITOR_ROLE` editor too; `test_supervisor_assign_editor_annotates_and_orders_by_workload` fails with `AttributeError: 'Account' object has no attribute 'available_workload_month'`.

- [ ] **Step 3: Implement the change**

Add imports to `wjs/plugins/wjs_review/views.py`. Add this line right before the existing `from .logic import (` block (around line 117):

```python
from .events.assignment import annotate_workload_availability, get_available_editor_parameters
```

Replace `_editors_with_keywords` (`views.py`, inside `class SupervisorAssignEditor`):

```python
    def _editors_with_keywords(self, search_text) -> QuerySet[Account]:
        """
        Provide the list of available, SECTION_EDITOR_ROLE editors, ranked as automatic assignment ranks them.

        Reuses Account.objects.get_editors_with_keywords for the keyword annotation (unchanged,
        still shared with automatic assignment via annotate_keyword_ranking), then intersects it
        with the same availability pipeline default_assign_editors_to_articles uses
        (get_available_editor_parameters + annotate_workload_availability, restricted to
        SECTION_EDITOR_ROLE) rather than rebuilding the candidate list from scratch. Unlike
        automatic assignment, this does NOT filter on available_workload_month: an EO must be able
        to see and manually pick an over-cap editor.

        The list is filtered by removing the current editor, if any, and any of the article's authors.
        """
        article = self.object.article
        article_authors = article.author_accounts.all()
        try:
            current_editor = WjsEditorAssignment.objects.get_current(self.object).editor
        except WjsEditorAssignment.DoesNotExist:
            current_editor = None

        section_editors = core_models.AccountRole.objects.filter(
            journal=article.journal,
            role=core_models.Role.objects.get(slug=constants.SECTION_EDITOR_ROLE),
        ).values_list("user")
        available_parameters = annotate_workload_availability(
            get_available_editor_parameters(journal=article.journal, editors=section_editors),
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
            search_filters = Q(Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text))
            qs = qs.filter(search_filters)
        return qs
```

`core_models` (`from core import models as core_models`), `F`, `OuterRef`, `Q`, `Subquery` are all already imported at the top of `views.py` — no other new imports needed beyond the `events.assignment` line above.

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 4 passed.

- [ ] **Step 5: Run the full `test_views.py` file to confirm no regression**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_views.py -q
```
Expected: all pass, including `test_editor_with_kdws_list_retrieval`/`test_editor_with_kdws_scoped_to_article_journal` (they call `Account.objects.get_editors_with_keywords` directly, unaffected by this change).

- [ ] **Step 6: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/views.py wjs/plugins/wjs_review/tests/test_views.py
git commit -m "fix: manual editor selection reuses the automatic-assignment availability/workload pipeline, restricted to section editors"
```

---

### Task 6: Template — show the workload column in manual selection

**Files:**
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html`

**Interfaces:**
- Consumes: `available_workload_month` (Task 5's new `Account` annotation) in `editor_list.html`.
- Produces: no new Python interface — a rendering-only change.

The "Editor Selection logic" spec lists "available monthly workload" as a required (non-Priority-2) "Value shown" for manual assignment, without gating it behind the existing `show_editor_keywords` per-journal flag (that flag only gates the keyword columns).

- [ ] **Step 1: Add the column header**

In `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html`, add a new `<th>` right after the existing "N. Matching Keywords" conditional block:

```
      {% if workflow.article.journal|show_editor_keywords %}
        <th scope="col" class="col-4">{% translate "Keywords" %}</th>
        <th scope="col" class="col-2">{% translate "N. Matching Keywords" %}</th>
      {% endif %}
      <th scope="col" class="col-1">{% translate "Available this month" %}</th>
      <th scope="col" class="col-1">{% translate "Special Issue" %}</th>
```

- [ ] **Step 2: Add the column cell**

In `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html`, add a matching `<td>` in the same position (after the keywords block, before the "Special Issue" `<td>` — the `managed_issue.issue_title` one):

```
    {% if workflow.article.journal|show_editor_keywords %}
      <td>{{ user.matching_keywords|join:", " }}</td>
      <td>{{ user.num_shared_kwds }}</td>
    {% endif %}
    <td>{{ user.available_workload_month }}</td>
    <td>
      {{ user.managed_issue.issue_title }}
    </td>
```

- [ ] **Step 3: Verify with the existing manual-selection view tests**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_views.py -k "test_eo_select_editor or test_eo_select_new_editor or test_supervisor_assign_editor" -q
```
Expected: still 6 passed (these tests exercise the view/queryset directly, not the rendered HTML, so they can't regress from a template-only change — this run is just a sanity check that nothing else broke). Then run `djlint` to confirm the templates still lint clean:

```bash
pre-commit run djlint-django --files wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html
```
Expected: passes (both new lines follow the file's existing 2-space-indentation convention and existing tag/attribute style, per `.claude/rules/templates-django.md`).

- [ ] **Step 4: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html
git commit -m "feat: show available monthly workload in manual editor selection table"
```

---

### Task 7: Full regression pass

**Files:** none changed — verification only.

**Interfaces:** none.

- [ ] **Step 1: Run the full automatic-assignment test file**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -q
```
Expected: all pass, zero failures.

- [ ] **Step 2: Run the full views test file**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_views.py -q
```
Expected: all pass.

- [ ] **Step 3: Run the other two test files that call `get_editors_with_keywords` directly, to confirm they're unaffected**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_communications.py wjs/plugins/wjs_review/tests/test_logic.py -q
```
Expected: no new failures — these tests exercise `get_editors_with_keywords` directly (unchanged by this plan, per Global Constraints) and are collateral-regression checks only.

- [ ] **Step 4: Run the full `wjs_review` suite**

```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest --create-db -n7 wjs/plugins/wjs_review -q
```
Expected: no failures. `--create-db` here to catch any migration-adjacent surprise, even though this plan adds no migrations (per `.claude/rules/tests.md`, reserve full-suite runs for exactly this kind of pre-merge check).

- [ ] **Step 5: `pre-commit run --all-files`**

```bash
pre-commit run --all-files
```
Expected: passes (per `.claude/rules/linting.md`) — this is the point to catch any stray formatting/import-order issue accumulated across Tasks 1–6.

- [ ] **Step 6: No commit needed for this task** (verification only — if any run fails, go back to the relevant task, fix, and re-commit there).

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-09-22-editor-assignment-review-findings-design.md`):
- D1 (keyword ranking never excludes) → Task 1 (new function) + Task 4 (wired in, old excluding function deleted). ✅
- D2 (two workload windows, excludes withdrawn) → Task 2. ✅
- D3 (shared final ordering, NULLS LAST) → Task 3. ✅
- D4 (drop the fallback branch) → Task 4. ✅
- D5 (manual view reuses the pipeline) → Task 5 (+ Task 6 for the display column). ✅
- D6 (SECTION_EDITOR_ROLE only for manual) → Task 5 (`available_parameters` built from the `SECTION_EDITOR_ROLE`-only `AccountRole` query; regression test asserts a plain-`EDITOR_ROLE` account is excluded). ✅
- Explicitly out of scope per the design (Priority 2 items, `#1253` exceptions, special-issue/JCOM bypasses): untouched by every task above — no task modifies `get_special_issue_parameters`, `jcom_assign_editors_to_articles`, or adds scoring/report features. ✅ (documented, not silently dropped)

**2. Placeholder scan:** No TBD/TODO; every test has concrete setup and assertions; every code step is a complete function/method body; no "similar to Task N" references — each task's code is written out in full even where it resembles an earlier task's shape (e.g. Task 5's `_editors_with_keywords` repeats the ordering logic from Task 3's `rank_editor_parameters` rather than pointing back at it, because it operates on `Account`, not `StaffWorkloadParameters` — the two orderings must independently exist since the manual view intersects, not reuses, the ranked `StaffWorkloadParameters` queryset for its final `order_by`).

**3. Type consistency:** `annotate_keyword_ranking(article: Article, users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]` (Task 1) is called with that exact signature in Task 3 and Task 4. `annotate_workload_availability(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]` (Task 2) likewise. `rank_editor_parameters(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]` (Task 3) is called on the Task 1+2 chain's output in Task 4's test and in `default_assign_editors_to_articles`. Annotation names (`matching_keywords_count`, `max_matching_keyword_weight`, `assignment_count_month`, `assignment_count_30d`, `effective_workload_cap`, `available_workload_month`, `available_workload_30d`) are consistent across Tasks 1–5's tests and implementation, including their reuse (via `Subquery`, not a shared function call) inside Task 5's `Account`-scoped annotations in `views.py`.
