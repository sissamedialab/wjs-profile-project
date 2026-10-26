# Automatic Editor Assignment — Keyword Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port keyword-based editor matching (currently only used for manual "Select new editor") into the *automatic* assignment path (`default_assign_editors_to_articles`), per the "Editor Selection logic" spec, without changing existing behavior for callers that have no matching keywords configured.

**Architecture:** Two new pure functions in `wjs/plugins/wjs_review/events/assignment.py` — `filter_parameters_by_matching_keywords` (filter+rank `StaffWorkloadParameters` by keyword overlap with the article) and `annotate_automatic_assignment_workload` (annotate current-month workload availability, capped at `min(workload, brake_on)` when `brake_on` is set). `default_assign_editors_to_articles` chains them for its non-special-issue branch and falls back to the existing `get_selected_editor_by_workload` when there is no keyword match. `jcom_assign_editors_to_articles` (JCOM/JCOMAL director assignment) and the special-issue branch are untouched — they keep ignoring keywords entirely. The existing manual-selection function `Account.objects.get_editors_with_keywords` (`wjs/plugins/wjs_review/users.py`) is left exactly as-is, kept as a shim for the manual "Select new editor" page.

**Tech Stack:** Django ORM (`QuerySet.annotate`/`Count`/`Max`/`Case`/`When`/`Least`), pytest + pytest-django, existing `wjs_review`/`wjs.jcom_profile` test fixtures.

**Spec:** `https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic#implementation-details` (section "Implementation details"). This plan implements the "Keywords matching" and "Automatic Editor assignment function" subsections only. The "Workload" subsection's *manual*-assignment variant and the "Manual editor selection view" subsection are explicitly **out of scope** (see Global Constraints below).

## Global Constraints

- `jcom_assign_editors_to_articles` (JCOM/JCOMAL director-based algorithm) **ignores keywords entirely** — do not add keyword matching to it. (Confirmed by repo owner.)
- The special-issue branch of `default_assign_editors_to_articles` (`get_special_issue_parameters`) also keeps its current pure-workload selection — the spec lists special issues as a bypass case still to be documented, so this port only touches the general (non-special-issue) branch.
- When `filter_parameters_by_matching_keywords` returns zero editors for a given article, fall back to selecting by workload among the full candidate set, using the **existing, unmodified** `get_selected_editor_by_workload` function. (Confirmed by repo owner.)
- The effective monthly workload cap for the new automatic-workload annotation is `min(workload, brake_on)` — **but** `brake_on` defaults to `0` for every existing editor (it means "no EO override" today, since it's never been wired into any logic), so a `brake_on` of `0` must be treated as *unset* and ignored, falling back to plain `workload`. Only `brake_on > 0` participates in the `min()`. (Confirmed by repo owner, with this necessary reading of "0 means unset" — taking `min()` literally against the current all-zero `brake_on` data would silently zero out every editor's automatic-assignment capacity.)
- `Account.objects.get_editors_with_keywords` (`wjs/plugins/wjs_review/users.py:313`) is kept unchanged, as a shim for `SupervisorAssignEditor` (manual editor selection). Do not touch `users.py`, `views.py::SupervisorAssignEditor`, or `editor_table.html` in this plan.
- Do not change `get_selected_editor_by_workload`, `get_available_editor_parameters`, or `get_special_issue_parameters` — they are reused as-is (the whole point of the fallback design is to leave their tested behavior untouched).
- Every new/changed test must run green with:
  `PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest <path> -q`
  (this is the interpreter with `wjs-submission`/`wjs.jcom_profile` installed in editable mode from these exact repo checkouts; do not use `janeway-upstream`, it is Django-4.2-pinned and out of date for this branch).

---

## File Structure

- **Modify:** `wjs/plugins/wjs_review/events/assignment.py`
  - Add imports: `Case`, `Count`, `Max`, `Q`, `When` from `django.db.models`; `Least` from `django.db.models.functions`; `from django.utils import timezone`.
  - Add `filter_parameters_by_matching_keywords(article, users_parameters)`.
  - Add `annotate_automatic_assignment_workload(users_parameters)`.
  - Modify `default_assign_editors_to_articles` to use both, with fallback.
- **Modify:** `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
  - Add unit tests for both new functions.
  - Add integration tests for the updated `default_assign_editors_to_articles` (keyword match wins, zero-match fallback, special issue unaffected).
  - Add a regression guard test proving `jcom_assign_editors_to_articles` still ignores keywords.

No other files change. `StaffWorkloadParameters`, `StaffKeyword`, `Keyword`, `WjsEditorAssignment` models are all reused as-is; no migration needed.

---

### Task 1: `filter_parameters_by_matching_keywords`

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `StaffWorkloadParameters` (existing model, `wjs/jcom_profile/models.py:196`, fields `user`, `journal`, `workload`, `brake_on`, `keywords` (M2M via `StaffKeyword`)), `StaffKeyword` (`wjs/jcom_profile/models.py:274`, fields `parameters`, `keyword`, `weight`), `Article.keywords` (Janeway core field).
- Produces: `filter_parameters_by_matching_keywords(article: Article, users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]`, annotated with `matching_keywords_count: int` and `max_matching_keyword_weight: int | None`, ordered `-max_matching_keyword_weight, -matching_keywords_count, id`. Used by Task 3.

- [ ] **Step 1: Write the failing tests**

Add to `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`, after the existing imports (extend the import block instead of duplicating it):

```python
from wjs.jcom_profile.models import JCOMProfile, StaffKeyword, StaffWorkloadParameters

from ..events.assignment import (
    filter_parameters_by_matching_keywords,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
)
```

(`StaffKeyword` and `filter_parameters_by_matching_keywords` are new imports; keep the rest of the existing import block as-is.)

Then append these tests at the end of the file:

```python
@pytest.mark.django_db
def test_filter_parameters_by_matching_keywords_excludes_no_match(article, editors, keywords):
    """Editors with no keyword overlap with the article are excluded entirely."""
    article.keywords.add(keywords[0])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)

    result = filter_parameters_by_matching_keywords(article, parameters)

    assert list(result) == []


@pytest.mark.django_db
def test_filter_parameters_by_matching_keywords_orders_by_weight_then_count(article, editors, keywords):
    """
    Ranking is: highest matching keyword weight first, then highest matching keyword count, then id.
    """
    article.keywords.add(keywords[0], keywords[1], keywords[2])
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    param_low_weight_two_matches, param_high_weight_one_match, param_no_match = parameters.order_by("id")

    StaffKeyword.objects.create(parameters=param_low_weight_two_matches, keyword=keywords[0], weight=1)
    StaffKeyword.objects.create(parameters=param_low_weight_two_matches, keyword=keywords[1], weight=1)
    StaffKeyword.objects.create(parameters=param_high_weight_one_match, keyword=keywords[0], weight=5)
    # param_no_match gets a keyword the article does not have, so it must stay excluded.
    StaffKeyword.objects.create(parameters=param_no_match, keyword=keywords[9], weight=99)

    result = list(filter_parameters_by_matching_keywords(article, parameters))

    assert result == [param_high_weight_one_match, param_low_weight_two_matches]
    assert result[0].max_matching_keyword_weight == 5
    assert result[0].matching_keywords_count == 1
    assert result[1].max_matching_keyword_weight == 1
    assert result[1].matching_keywords_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_filter_parameters_by_matching_keywords" -q
```
Expected: collection error / `ImportError: cannot import name 'filter_parameters_by_matching_keywords'`.

- [ ] **Step 3: Implement the function**

In `wjs/plugins/wjs_review/events/assignment.py`, update the `django.db.models` import line:

```python
from django.db.models import Case, Count, F, FloatField, Func, Max, OuterRef, Q, QuerySet, Subquery, When
```

Add the function right after `get_available_editor_parameters` (before `default_assign_editors_to_articles`):

```python
def filter_parameters_by_matching_keywords(
    article: Article,
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Filter and rank StaffWorkloadParameters by how well they match the article's keywords.

    Only parameters that share at least one keyword with the article are kept. The result is
    annotated with the number of matching keywords and the highest configured weight among
    them, and ordered so the best match (highest weight, then most matching keywords) comes
    first.

    :param article: The article whose keywords are matched against.
    :type article: Article
    :param users_parameters: The candidate StaffWorkloadParameters to filter/rank.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The matching StaffWorkloadParameters, annotated and ordered best-match-first.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    article_keywords = article.keywords.all()
    return (
        users_parameters.filter(keywords__in=article_keywords)
        .annotate(
            matching_keywords_count=Count(
                "staffkeyword__keyword",
                filter=Q(staffkeyword__keyword__in=article_keywords),
                distinct=True,
            ),
            max_matching_keyword_weight=Max(
                "staffkeyword__weight",
                filter=Q(staffkeyword__keyword__in=article_keywords),
            ),
        )
        .distinct()
        .order_by(
            "-max_matching_keyword_weight",
            "-matching_keywords_count",
            "id",
        )
    )
```

Note the added `.distinct()`: `users_parameters.filter(keywords__in=article_keywords)` joins through the M2M and produces one row per matching keyword, so a parameter matching 2 article keywords would otherwise appear twice before annotation collapses counts — `.distinct()` keeps one row per `StaffWorkloadParameters`.

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat: add filter_parameters_by_matching_keywords for automatic editor assignment"
```

---

### Task 2: `annotate_automatic_assignment_workload`

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `WjsEditorAssignment` (`wjs/plugins/wjs_review/models.py:1785`, has `editor`, `assigned` (DateTimeField, defaults to `timezone.now()`) inherited from Janeway's `review.models.EditorAssignment`). `StaffWorkloadParameters.workload`, `.brake_on` (both `PositiveSmallIntegerField`, default `0`).
- Produces: `annotate_automatic_assignment_workload(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]`, annotated with `assignment_count: int`, `effective_workload_cap: int`, `automatic_available_workload: int`, filtered to `automatic_available_workload__gt=0`, ordered `-automatic_available_workload, id`. Used by Task 3.

- [ ] **Step 1: Write the failing tests**

Extend the import in `test_automatic_article_assignment.py` from Task 1 to also bring in the new function:

```python
from ..events.assignment import (
    annotate_automatic_assignment_workload,
    filter_parameters_by_matching_keywords,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
)
```

Append these tests:

```python
@pytest.mark.django_db
def test_annotate_automatic_assignment_workload_uses_smaller_of_workload_and_brake_on(article, editors, journal):
    """When brake_on is set (> 0), the effective cap is min(workload, brake_on), not workload alone."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = 3
    parameters.save()

    result = annotate_automatic_assignment_workload(
        StaffWorkloadParameters.objects.filter(pk=parameters.pk)
    ).get()

    assert result.effective_workload_cap == 3
    assert result.automatic_available_workload == 3


@pytest.mark.django_db
def test_annotate_automatic_assignment_workload_ignores_zero_brake_on(article, editors, journal):
    """A brake_on of 0 (the model default) means "no EO override": fall back to plain workload."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 10
    parameters.brake_on = 0
    parameters.save()

    result = annotate_automatic_assignment_workload(
        StaffWorkloadParameters.objects.filter(pk=parameters.pk)
    ).get()

    assert result.effective_workload_cap == 10
    assert result.automatic_available_workload == 10


@pytest.mark.django_db
def test_annotate_automatic_assignment_workload_counts_only_current_month(article, editors, journal, fake_request):
    """Only WjsEditorAssignment rows assigned in the current calendar month count against the cap."""
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

    result = annotate_automatic_assignment_workload(
        StaffWorkloadParameters.objects.filter(pk=parameters.pk)
    ).get()

    assert result.assignment_count == 0
    assert result.automatic_available_workload == 1


@pytest.mark.django_db
def test_annotate_automatic_assignment_workload_excludes_exhausted_editors(article, editors, journal):
    """An editor with no availability left this month is filtered out, not just ranked last."""
    parameters = StaffWorkloadParameters.objects.filter(user=editors[0]).first()
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()

    WjsEditorAssignment.objects.create(
        article=article,
        editor=editors[0],
        editor_type="section-editor",
    )

    result = annotate_automatic_assignment_workload(
        StaffWorkloadParameters.objects.filter(pk=parameters.pk)
    )

    assert list(result) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_annotate_automatic_assignment_workload" -q
```
Expected: `ImportError: cannot import name 'annotate_automatic_assignment_workload'`.

- [ ] **Step 3: Implement the function**

Update the `django.db.models.functions` import line in `assignment.py`:

```python
from django.db.models.functions import Cast, Coalesce, Least, NullIf
```

Add `from django.utils import timezone` near the other stdlib/Django imports at the top of the file.

Add the function directly after `filter_parameters_by_matching_keywords`:

```python
def annotate_automatic_assignment_workload(
    users_parameters: QuerySet[StaffWorkloadParameters],
) -> QuerySet[StaffWorkloadParameters]:
    """
    Annotate StaffWorkloadParameters with their automatic-assignment workload availability.

    The effective monthly cap is the smaller of ``workload`` and ``brake_on``. ``brake_on``
    defaults to 0, which means "no EO override" (it is never set by default, and 0 must not be
    read as "zero capacity"): a 0 ``brake_on`` is ignored and the cap falls back to plain
    ``workload``. Only WjsEditorAssignment rows assigned in the current calendar month count
    against the cap, regardless of the assignment's current review state. Parameters with no
    availability left this month are excluded entirely, not just ranked last.

    :param users_parameters: The candidate StaffWorkloadParameters to annotate.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The queryset annotated with ``assignment_count``, ``effective_workload_cap`` and
        ``automatic_available_workload``, restricted to parameters with availability left,
        ordered with the most available editor first.
    :rtype: QuerySet[StaffWorkloadParameters]
    """
    from ..models import WjsEditorAssignment

    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assigned_this_month = (
        WjsEditorAssignment.objects.filter(editor=OuterRef("user_id"), assigned__gte=month_start)
        .annotate(count=Func(F("id"), function="Count"))
        .values("count")
    )
    return (
        users_parameters.annotate(
            assignment_count=Coalesce(Subquery(assigned_this_month), 0),
            effective_workload_cap=Case(
                When(brake_on__gt=0, then=Least(F("workload"), F("brake_on"))),
                default=F("workload"),
            ),
            automatic_available_workload=F("effective_workload_cap") - F("assignment_count"),
        )
        .filter(automatic_available_workload__gt=0)
        .order_by("-automatic_available_workload", "id")
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat: add annotate_automatic_assignment_workload for automatic editor assignment"
```

---

### Task 3: Wire both into `default_assign_editors_to_articles`

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `filter_parameters_by_matching_keywords` and `annotate_automatic_assignment_workload` from Tasks 1–2; existing `get_available_editor_parameters`, `get_special_issue_parameters`, `get_selected_editor_by_workload`, `BaseAssignToEditor` (`wjs/plugins/wjs_review/logic.py`).
- Produces: `default_assign_editors_to_articles(article, **kwargs) -> Optional["WjsEditorAssignment"]` — same public signature as before, now keyword-aware for the non-special-issue branch.

- [ ] **Step 1: Write the failing tests**

Append to `test_automatic_article_assignment.py`:

```python
@pytest.mark.django_db
def test_default_assign_editors_prefers_keyword_match_over_workload(
    review_settings, article, editors, coauthors_setting,
):
    """
    An editor with a matching keyword is picked over an editor with more available workload
    but no matching keyword.
    """
    keyword = submission_models.Keyword.objects.create(word="test-keyword")
    article.journal.keywords.add(keyword)
    article.keywords.add(keyword)
    article.primary_issue = None
    article.save()

    high_workload_no_match, low_workload_with_match, _ = StaffWorkloadParameters.objects.filter(
        user__in=editors
    ).order_by("id")
    high_workload_no_match.workload = 10
    high_workload_no_match.save()
    low_workload_with_match.workload = 1
    low_workload_with_match.save()
    StaffKeyword.objects.create(parameters=low_workload_with_match, keyword=keyword, weight=1)

    assignment = default_assign_editors_to_articles(article)

    assert assignment is not None
    assert assignment.editor == low_workload_with_match.user


@pytest.mark.django_db
def test_default_assign_editors_falls_back_to_workload_when_no_keyword_match(
    review_settings, article, editors, coauthors_setting,
):
    """With no keyword overlap for any candidate, selection falls back to the existing workload logic."""
    article.primary_issue = None
    article.save()
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors)
    expected_editor = get_selected_editor_by_workload(parameters, journal=article.journal)

    assignment = default_assign_editors_to_articles(article)

    assert assignment is not None
    assert assignment.editor == expected_editor


@pytest.mark.django_db
def test_jcom_assign_editors_ignores_keywords(review_settings, article, directors, coauthors_setting):
    """
    JCOM/JCOMAL director assignment must keep ignoring keywords: a director with a matching
    keyword but less available workload must NOT be preferred over the higher-workload one.
    """
    keyword = submission_models.Keyword.objects.create(word="test-keyword")
    article.journal.keywords.add(keyword)
    article.keywords.add(keyword)
    article.primary_issue = None
    article.save()

    high_workload_no_match, low_workload_with_match, _ = StaffWorkloadParameters.objects.filter(
        user__in=directors
    ).order_by("id")
    high_workload_no_match.workload = 10
    high_workload_no_match.save()
    low_workload_with_match.workload = 1
    low_workload_with_match.save()
    StaffKeyword.objects.create(parameters=low_workload_with_match, keyword=keyword, weight=99)

    assignment = jcom_assign_editors_to_articles(article)

    assert assignment is not None
    assert assignment.editor == high_workload_no_match.user
```

Add `submission_models` and the two assignment functions to the imports at the top of the test file:

```python
from submission import models as submission_models
from submission.models import Article

from ..events.assignment import (
    annotate_automatic_assignment_workload,
    default_assign_editors_to_articles,
    filter_parameters_by_matching_keywords,
    get_available_editor_parameters,
    get_select_eo_by_workload,
    get_selected_editor_by_workload,
    jcom_assign_editors_to_articles,
)
```

(`submission.models.Article` is likely already imported as `from submission.models import Article`; keep both the plain `Article` name and the `submission_models` alias, since existing tests use bare `Article` while the new ones use `submission_models.Keyword.objects.create(...)`.)

- [ ] **Step 2: Run tests to verify they fail or reveal current behavior**

Run:
```bash
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "test_default_assign_editors_prefers_keyword_match_over_workload or test_default_assign_editors_falls_back_to_workload_when_no_keyword_match or test_jcom_assign_editors_ignores_keywords" -q
```
Expected: `test_default_assign_editors_prefers_keyword_match_over_workload` FAILS (current code picks by workload only, ignoring the keyword match); the other two should already PASS against the current code (this confirms the fallback path and the JCOM path are pre-existing behavior we must not break).

- [ ] **Step 3: Implement the change**

Replace `default_assign_editors_to_articles` in `wjs/plugins/wjs_review/events/assignment.py`:

```python
def default_assign_editors_to_articles(article: Article, **kwargs) -> Optional["WjsEditorAssignment"]:
    """Assign editors to article for review. Default algorithm."""
    from ..logic import BaseAssignToEditor

    is_special_issue = bool(article.primary_issue and article.primary_issue.managing_editors.exists())
    if is_special_issue:
        # Special issues keep their own dedicated editor pool and pure-workload selection;
        # keyword matching is not applied here (see spec's "bypasses" section).
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
        user = None
        if not is_special_issue:
            matched_parameters = annotate_automatic_assignment_workload(
                filter_parameters_by_matching_keywords(article, parameters)
            ).order_by(
                "-max_matching_keyword_weight",
                "-matching_keywords_count",
                "-automatic_available_workload",
                "id",
            )
            if matched_parameter := matched_parameters.first():
                user = matched_parameter.user
        if user is None:
            user = get_selected_editor_by_workload(parameters, journal=article.journal)
        if user:
            assignment = BaseAssignToEditor(editor=user, article=article, request=request, first_assignment=True).run()
            return assignment
```

`jcom_assign_editors_to_articles` is **not** modified.

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2. Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat: prefer keyword-matched editors in automatic assignment, fall back to workload"
```

---

### Task 4: Full regression pass

**Files:** none changed — verification only.

**Interfaces:** none.

- [ ] **Step 1: Run the full automatic-assignment test file**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -q
```
Expected: all tests pass (16 pre-existing + 9 new = 25), zero failures. The pre-existing tests must pass **unchanged** — they exercise editors/directors with no `StaffKeyword` rows, so `filter_parameters_by_matching_keywords` returns empty for them and the fallback path (identical to today's behavior) is what actually runs.

- [ ] **Step 2: Run the broader wjs_review test suite for collateral regressions**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
PYTHONPATH="/home/yakky/Projects/projects/sissa-1.8/janeway/src:/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project" /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_views.py wjs/plugins/wjs_review/tests/test_logic.py -q
```
Expected: no new failures (these files exercise `get_editors_with_keywords`/`SupervisorAssignEditor`, which this plan does not touch — they should be unaffected either way; running them just confirms that).

- [ ] **Step 3: No commit needed for this task** (verification only — if either run fails, go back to the relevant task, fix, and re-commit there).

---

## Self-Review

**1. Spec coverage:**
- "Keywords matching" (filter + annotate + order function in `assignment.py`) → Task 1. ✅
- "Workload" (annotate function in `assignment.py`) → Task 2, automatic-assignment variant only (manual variant explicitly out of scope per Global Constraints). ✅
- "Automatic Editor assignment function" (wire into `default_assign_editors_to_articles`) → Task 3. ✅
- "Manual editor selection view" → explicitly out of scope (Global Constraints); `get_editors_with_keywords` kept as shim, untouched. ✅ (documented, not silently dropped)
- "Priority 2" (scoring report, select-editor UI scores) → explicitly out of scope, spec itself marks it lower priority. ✅ (documented)
- JCOM/JCOMAL bypass → Task 3, `jcom_assign_editors_to_articles` untouched + regression test. ✅
- Special issue bypass → Task 3, `is_special_issue` branch untouched. ✅

**2. Placeholder scan:** No TBD/TODO, every test has concrete assertions and setup, every code step is a complete function body, no "similar to Task N" references.

**3. Type consistency:** `filter_parameters_by_matching_keywords(article: Article, users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]` (Task 1) is called with exactly that signature in Task 3. `annotate_automatic_assignment_workload(users_parameters: QuerySet[StaffWorkloadParameters]) -> QuerySet[StaffWorkloadParameters]` (Task 2) is called with exactly that signature (chained onto Task 1's output) in Task 3. Annotation names (`matching_keywords_count`, `max_matching_keyword_weight`, `assignment_count`, `effective_workload_cap`, `automatic_available_workload`) are consistent across all three tasks' tests and implementation.
