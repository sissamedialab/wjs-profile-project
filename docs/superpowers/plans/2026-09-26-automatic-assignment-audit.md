# Automatic Editor Assignment — Audit Trail & Historical Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record why every automatic editor assignment picked its editor (permanent `AutomaticAssignmentAudit` log and an EO/director audit page), and add a one-off `replay_automatic_assignment` command that resets historical articles on a copy instance and replays them in submission order through the real assignment code, writing a CSV comparison report.

**Architecture:** `get_selected_editor_by_workload` changes to return the winning annotated `StaffWorkloadParameters` row, so every branch of `default_assign_editors_to_articles`/`jcom_assign_editors_to_articles` has the numbers it used. A new `record_automatic_assignment_audit` helper writes one append-only `AutomaticAssignmentAudit` row next to `BaseAssignToEditor(...).run()`, in the same transaction. A `FilterView` lists the rows for EO/directors. A new `logic__replay.py` holds two business-logic dataclasses: `ResetArticleForReplay` resets one article, and `ReplayAutomaticAssignment` snapshots and resets the whole batch first, then replays each article under `freezegun.freeze_time(article.date_submitted)` via the unmodified `dispatch_assignment`. A thin management command wraps the replay and writes the CSV.

**Tech Stack:** Django 5.2 ORM, django-fsm-2, django-filter 25, freezegun (already imported at runtime by `wjs/jcom_profile/import_logic.py`), pytest + pytest-django, existing `wjs_review`/`wjs.jcom_profile` fixtures.

**Spec:** `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md` (issue wjs/specs#3136).

## Global Constraints

- **Automatic assignment only.** Don't touch `Account.objects.get_editors_with_keywords`, `SupervisorAssignEditor`, `get_select_eo_by_workload` or `dispatch_eo_assignment`.
- **Phase 1 only.** Capture only the *chosen* editor's own numbers. Don't compare candidates and don't record discard reasons.
- The audit model is **not** FK'd to `WjsEditorAssignment`. It is an independent, append-only log: `article` uses `on_delete=CASCADE`, and `editor` uses `on_delete=SET_NULL, null=True`.
- `workload_available`/`workload_cap` hold **whichever metric actually ran**:
  - keyword path: `automatic_available_workload`/`effective_workload_cap`
  - fallback, special-issue and JCOM paths: `available_workload`/`workload` from `get_selected_editor_by_workload`
- Write an audit row **only** when an editor was actually assigned, i.e. after `BaseAssignToEditor(...).run()` succeeds.
- `algorithm` values:
  - `DEFAULT`: default algorithm, keyword or fallback branch
  - `DEFAULT_SPECIAL_ISSUE`: special-issue branch of the default algorithm
  - `JCOM`: every `jcom_assign_editors_to_articles` assignment, special issue or not
- The replay command must only run on an imported copy of production. It refuses to run without `--confirm-copy-instance`, and it always swaps in the locmem email backend so no real email goes out.
- The replay's "original editor" is the **first editor ever assigned** to the article, not the current or latest one. This was agreed with the repo owner and supersedes the spec's "current, else latest past" wording.
- The reset moves `ArticleWorkflow.state` back to **`SUBMITTED`** and `Article.stage` back to `STAGE_UNASSIGNED`.
- The replay orders articles by **`Article.date_submitted` ascending** (tie-break `pk`), not by `WjsEditorAssignment.assigned`.
- The replay calls the **real, unmodified `dispatch_assignment(article)`**. Don't add a "test mode" branch to production code.
- Typesetting-state reset is out of scope. Articles with any `TypesettingAssignment` are **skipped**, reported with a reason, and left untouched.
- Follow the repo's architecture rules (`.claude/rules/architecture-django.md`):
  - business logic lives in dataclasses with a `run()` wrapped in `transaction.atomic()`, and precondition failures raise `ValidationError`
  - model filtering goes through queryset methods
  - views stay thin
- Style: black at 119 columns, double quotes, docstrings on every public function (imperative, ending with `.`), `gettext_lazy` for user-facing strings, `verbose_name` on every model field, templates with 2-space indentation and named `{% endblock name %}`.
- URL names and paths use hyphens: `wjs-automatic-assignment-audit`, `automatic-assignment-audit/`.
- Tests run from `janeway/src` (from here on, "run test" means this command):
  `cd /home/yakky/Projects/sissa/janeway/src && pytest ../../wjs-profile-project/<path> -q`
  Use `--create-db` for the first run after adding the migration.
- Branch: implement on `feature/issue-3136-automatic-assignment-audit`, cut from the current `docs/automatic-assignment-audit-design` branch (it carries the keyword-matching work this builds on). Never commit to `wjs-develop`. Commit messages follow Conventional Commits, in English. If the user chose end-of-flow commit review, stage the files and skip the `git commit` steps.

## Review Focus

1. **Running against a real instance or sending real emails.** The command refuses to run without `--confirm-copy-instance`, and replay never sends email through the real backend. Tests are in Task 6.
2. **Articles that reached typesetting.** They are skipped with `skipped_reason`: no assignment is deleted and their state is unchanged. The run doesn't crash. Tests are in Task 5.
3. **Historical assignments of later batch articles skewing workload.** An editor who historically got many batch articles must not look "full" when the first batch article is replayed. This is why the whole batch is reset before any article is replayed. Test is in Task 5.
4. **Assignments dated after the simulated "now".** They must not count against the monthly cap: `annotate_automatic_assignment_workload` gets an `assigned__lte=now` bound. Test is in Task 5.
5. **Replay finds no editor for an article.** The report row gets an empty `new_editor` and `match=False`, no audit row is written, and the workflow moves to `EDITOR_TO_BE_SELECTED` like the real `process_submission`. Test is in Task 5.

---

## File Structure

- **Modify** `wjs/plugins/wjs_review/events/assignment.py`: `get_selected_editor_by_workload` return type, new `record_automatic_assignment_audit`, capture points in both dispatch functions, and the upper time bound in `annotate_automatic_assignment_workload`.
- **Modify** `wjs/plugins/wjs_review/models.py`: new `AutomaticAssignmentAudit` model.
- **Modify** `wjs/plugins/wjs_review/managers.py`: new `AutomaticAssignmentAuditQuerySet`.
- **Create** `wjs/plugins/wjs_review/migrations/0018_automaticassignmentaudit.py`: generated.
- **Modify** `wjs/plugins/wjs_review/filters.py`: new `AutomaticAssignmentAuditFilter`.
- **Modify** `wjs/plugins/wjs_review/views.py`: new `AutomaticAssignmentAuditList`, plus `related_views` entries.
- **Modify** `wjs/plugins/wjs_review/urls.py`: new route.
- **Create** `wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/audit_list.html`.
- **Create** `wjs/plugins/wjs_review/logic__replay.py`: replay business logic.
- **Create** `wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py`.
- **Modify** `setup.cfg`: add `freezegun` to `install_requires`. It is now a declared runtime dependency; before this it was pulled in only transitively.
- **Tests:**
  - modify `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
  - create `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py`
  - create `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`
- **Docs:** modify `wjs/plugins/wjs_review/CLAUDE.md` (short section on the audit and the replay command).

---

### Task 1: `get_selected_editor_by_workload` returns the annotated parameter row

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (`get_selected_editor_by_workload`, fallback branch of `default_assign_editors_to_articles`, `jcom_assign_editors_to_articles`)
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Produces: `get_selected_editor_by_workload(users_parameters: QuerySet[StaffWorkloadParameters], journal: Journal) -> StaffWorkloadParameters | None`. The returned row carries `.user`, `.workload`, `.assignment_count` and `.available_workload`. Tasks 3 and 5 rely on this.
- Produces (test-only): `_selected_editor(parameters, journal) -> Account | None` helper in the test module.

- [ ] **Step 1: Write the failing test and the test helper**

In `test_automatic_article_assignment.py`, add this helper right after the `RANDOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS` constant:

```python
def _selected_editor(parameters, journal):
    """Return the Account chosen by get_selected_editor_by_workload, or None when nobody is available."""
    parameter = get_selected_editor_by_workload(parameters, journal=journal)
    return parameter.user if parameter else None
```

Add this new test after `test_get_selected_editor_by_workload_include_by_state`:

```python
@pytest.mark.django_db
def test_get_selected_editor_by_workload_returns_annotated_parameter(review_settings, editors, journal):
    """The winning StaffWorkloadParameters row is returned, carrying its workload annotations."""
    StaffWorkloadParameters.objects.filter(journal=journal).update(workload=3)
    StaffWorkloadParameters.objects.filter(user=editors[1], journal=journal).update(workload=7)

    parameter = get_selected_editor_by_workload(
        StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal),
        journal=journal,
    )

    assert isinstance(parameter, StaffWorkloadParameters), "the parameter row must be returned, not the Account"
    assert parameter.user == editors[1], "the editor with the most available workload must win"
    assert parameter.workload == 7, "the returned row must carry its own workload"
    assert parameter.available_workload == 7, "with no open assignments, available workload equals workload"


@pytest.mark.django_db
def test_get_selected_editor_by_workload_returns_none_without_candidates(review_settings, journal):
    """With an empty candidate set, None is returned (callers must not dereference .user blindly)."""
    parameter = get_selected_editor_by_workload(StaffWorkloadParameters.objects.none(), journal=journal)

    assert parameter is None, "no candidates must yield None"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "returns_annotated_parameter or returns_none_without_candidates"`
Expected: `test_get_selected_editor_by_workload_returns_annotated_parameter` FAILS on the `isinstance` assertion. The `None` test already passes, which is expected: it pins behavior that must survive the change.

- [ ] **Step 3: Change the function**

In `events/assignment.py`, replace the signature, docstring tail and return block of `get_selected_editor_by_workload`:

```python
def get_selected_editor_by_workload(
    users_parameters: QuerySet[StaffWorkloadParameters], journal: Journal
) -> StaffWorkloadParameters | None:
    """
    Select the user with the highest workload remaining, regardless the algorithm.

    The workload is calculated as the number of assigned papers in a state where the editor is required to
    process the paper (ie: accepted / rejected / published papers are not considered because they do not generate
    an actual workload on the editor).

    :param users_parameters: The StaffWorkloadParameters for the selected users.
    :type users_parameters: QuerySet[StaffWorkloadParameters]
    :return: The winning StaffWorkloadParameters, annotated with ``assignment_count`` and ``available_workload``
        (so callers can both read ``.user`` and record the numbers the choice was based on); None if no user
        is available.
    :rtype: StaffWorkloadParameters | None
    """
```

Keep the body as it is up to `annotated_parameters = ...`, then replace the tail with:

```python
    return annotated_parameters.order_by("-available_workload", "id").first()
```

- [ ] **Step 4: Update the two production call sites**

In `default_assign_editors_to_articles`, replace:

```python
        if user is None:
            # Fallback: caps on open-articles-needing-an-editor (no monthly window), unlike the
            # keyword-matched path above. See the docstring note for details.
            user = get_selected_editor_by_workload(parameters, journal=article.journal)
```

with:

```python
        if user is None:
            # Fallback: caps on open-articles-needing-an-editor (no monthly window), unlike the
            # keyword-matched path above. See the docstring note for details.
            parameter = get_selected_editor_by_workload(parameters, journal=article.journal)
            user = parameter.user if parameter else None
```

In `jcom_assign_editors_to_articles`, replace:

```python
        user = get_selected_editor_by_workload(parameters, journal=article.journal)
```

with:

```python
        parameter = get_selected_editor_by_workload(parameters, journal=article.journal)
        user = parameter.user if parameter else None
```

Also, in the `default_assign_editors_to_articles` docstring, replace the last sentence "and `get_selected_editor_by_workload` itself is intentionally left unchanged." with "and `get_selected_editor_by_workload` keeps its selection metric unchanged (it now returns the annotated parameter row instead of the bare Account, so the audit trail can record its numbers)."

- [ ] **Step 5: Update the existing test call sites to use the helper**

In `test_automatic_article_assignment.py`, make these exact replacements (line numbers are as of commit `244e5a73`):
- Lines 90, 339, 390 and 443: `expected_editor = get_selected_editor_by_workload(parameters, journal=article.journal)` becomes `expected_editor = _selected_editor(parameters, journal=article.journal)`.
- Lines 575–582 (inside `test_workload_decrease_editor`): replace each `get_selected_editor_by_workload(parameters, journal=article.journal)` with `_selected_editor(parameters, journal=article.journal)`. There are three occurrences.
- Line 775 (`test_get_selected_editor_by_workload`): replace `editor_selected = get_selected_editor_by_workload(` with `editor_selected = _selected_editor(`. The arguments are unchanged.
- Line 830 (`test_get_selected_editor_by_workload_include_by_state`): same replacement.
- Line 1181 (`test_default_assign_editors_falls_back_to_workload_when_no_keyword_match`): `expected_editor = _selected_editor(parameters, journal=article.journal)`.

Check that nothing was missed:
`grep -n "get_selected_editor_by_workload(" wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
Expected: only the import line, the helper body and the two new tests.

- [ ] **Step 6: Run the whole assignment test module**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "refactor(wjs_review): return the annotated parameter row from get_selected_editor_by_workload"
```

---

### Task 2: `AutomaticAssignmentAudit` model, queryset and migration

**Files:**
- Modify: `wjs/plugins/wjs_review/managers.py`
- Modify: `wjs/plugins/wjs_review/models.py` (append after `BlacklistedAuthorEmail`)
- Create: `wjs/plugins/wjs_review/migrations/0018_automaticassignmentaudit.py` (generated)
- Test: `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py` (new)

**Interfaces:**
- Produces:
  - `AutomaticAssignmentAudit` with the fields `article`, `editor`, `created_at`, `algorithm`, `matched_by_keyword`, `matching_keywords_count`, `max_matching_keyword_weight`, `workload_available` and `workload_cap`
  - `AutomaticAssignmentAudit.Algorithms` (`DEFAULT="default"`, `DEFAULT_SPECIAL_ISSUE="default_special_issue"`, `JCOM="jcom"`)
  - `AutomaticAssignmentAudit.objects.for_journal(journal) -> QuerySet` and `.latest_for_article(article) -> AutomaticAssignmentAudit | None`
- Consumed by: Tasks 3–6.

- [ ] **Step 1: Write the failing tests**

Create `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py`:

```python
"""Tests for the automatic-assignment audit trail."""

import pytest
from django.contrib.auth import get_user_model
from submission.models import Article

from ..models import AutomaticAssignmentAudit, WjsEditorAssignment

Account = get_user_model()


def _make_audit(article, editor, **kwargs):
    defaults = {
        "algorithm": AutomaticAssignmentAudit.Algorithms.DEFAULT,
        "matched_by_keyword": False,
        "workload_available": 3,
        "workload_cap": 5,
    }
    defaults.update(kwargs)
    return AutomaticAssignmentAudit.objects.create(article=article, editor=editor, **defaults)


@pytest.mark.django_db
def test_audit_survives_editor_assignment_deletion(article, editors):
    """Deleting the WjsEditorAssignment (deassignment/reassignment) must not delete the audit row."""
    assignment = WjsEditorAssignment.objects.create(article=article, editor=editors[0], editor_type="section-editor")
    audit = _make_audit(article, editors[0])

    assignment.delete()

    assert AutomaticAssignmentAudit.objects.filter(pk=audit.pk).exists(), "audit rows are an independent log"


@pytest.mark.django_db
def test_audit_editor_set_null_on_account_deletion(article, editors):
    """Deleting the chosen editor's account keeps the audit row with a null editor."""
    audit = _make_audit(article, editors[0])

    Account.objects.filter(pk=editors[0].pk).delete()
    audit.refresh_from_db()

    assert audit.editor is None, "editor FK must be SET_NULL"


@pytest.mark.django_db
def test_audit_for_journal_scopes_rows(article, editors, journal_factory):
    """for_journal() returns only rows whose article belongs to the given journal."""
    other_journal = journal_factory("JOTHER")
    other_article = Article.objects.create(journal=other_journal, title="Other", owner=editors[0])
    own = _make_audit(article, editors[0])
    _make_audit(other_article, editors[0])

    assert list(AutomaticAssignmentAudit.objects.for_journal(article.journal)) == [own], "only own-journal rows"


@pytest.mark.django_db
def test_audit_latest_for_article_returns_newest(article, editors):
    """latest_for_article() returns the most recently created row (multiple rows per article are allowed)."""
    _make_audit(article, editors[0])
    newest = _make_audit(article, editors[1])

    assert AutomaticAssignmentAudit.objects.latest_for_article(article) == newest, "newest row must win"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py`
Expected: collection ERROR, `ImportError: cannot import name 'AutomaticAssignmentAudit'`.

- [ ] **Step 3: Add the queryset**

Append to `wjs/plugins/wjs_review/managers.py`:

```python
class AutomaticAssignmentAuditQuerySet(models.QuerySet):
    def for_journal(self, journal: Journal) -> QuerySet:
        """Return the audit rows for articles of the given journal."""
        return self.filter(article__journal=journal)

    def latest_for_article(self, article: Article) -> Optional["AutomaticAssignmentAudit"]:
        """Return the most recent audit row for the given article, or None."""
        return self.filter(article=article).order_by("-created_at", "-pk").first()
```

Make sure `Journal`, `Article`, `Optional` and `QuerySet` are imported at the top of `managers.py`, and add any that are missing. Check with `sed -n 1,20p wjs/plugins/wjs_review/managers.py`. `Article` is already used in the `WjsEditorAssignmentQuerySet` annotations.

- [ ] **Step 4: Add the model**

In `models.py`, add `AutomaticAssignmentAuditQuerySet` to the existing `from .managers import (...)` block, then append:

```python
class AutomaticAssignmentAudit(models.Model):
    """
    Why the automatic assignment picked a given editor, captured at decision time.

    Append-only log, deliberately not linked to WjsEditorAssignment: that row is hard-deleted on
    deassignment/reassignment, which is exactly when the audit history is most useful.
    ``workload_available``/``workload_cap`` hold whichever workload metric actually ran (monthly cap for
    keyword-matched assignments, open-articles metric for fallback/special-issue/JCOM assignments).
    """

    class Algorithms(models.TextChoices):
        DEFAULT = "default", _("Default")
        DEFAULT_SPECIAL_ISSUE = "default_special_issue", _("Default (special issue)")
        JCOM = "jcom", _("JCOM")

    article = models.ForeignKey(
        "submission.Article",
        verbose_name=_("Article"),
        on_delete=models.CASCADE,
        related_name="automatic_assignment_audits",
    )
    editor = models.ForeignKey(
        Account,
        verbose_name=_("Editor"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)
    algorithm = models.CharField(_("Algorithm"), max_length=32, choices=Algorithms.choices)
    matched_by_keyword = models.BooleanField(_("Matched by keyword"), default=False)
    matching_keywords_count = models.PositiveIntegerField(_("Matching keywords count"), null=True, blank=True)
    max_matching_keyword_weight = models.PositiveIntegerField(
        _("Max matching keyword weight"), null=True, blank=True
    )
    workload_available = models.IntegerField(_("Available workload"), null=True, blank=True)
    workload_cap = models.IntegerField(_("Workload cap"), null=True, blank=True)

    objects = AutomaticAssignmentAuditQuerySet.as_manager()

    class Meta:
        verbose_name = _("Automatic assignment audit")
        verbose_name_plural = _("Automatic assignment audits")
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.article_id} → {self.editor_id} ({self.get_algorithm_display()})"
```

- [ ] **Step 5: Generate the migration**

Run:

```bash
cd /home/yakky/Projects/sissa/janeway/src && python manage.py makemigrations wjs_review -n automaticassignmentaudit
```

Expected: `wjs/plugins/wjs_review/migrations/0018_automaticassignmentaudit.py` is created with a single `CreateModel`, depending on `0017_blacklisted_authoremail`. Read it and delete any unrelated operations (such as spurious translation-field alterations). Then run `python manage.py makemigrations wjs_review --check --dry-run`. Expected: `No changes detected`.

- [ ] **Step 6: Run the tests**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py --create-db`
Expected: 4 PASS.

- [ ] **Step 7: Commit**

```bash
git add wjs/plugins/wjs_review/managers.py wjs/plugins/wjs_review/models.py \
  wjs/plugins/wjs_review/migrations/0018_automaticassignmentaudit.py \
  wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py
git commit -m "feat(wjs_review): add AutomaticAssignmentAudit model"
```

---

### Task 3: Capture audit rows in the automatic assignment functions

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes: `get_selected_editor_by_workload(...) -> StaffWorkloadParameters | None` (Task 1), and `AutomaticAssignmentAudit` (Task 2).
- Produces: `record_automatic_assignment_audit(*, article: Article, editor: Account, algorithm: str, matched_by_keyword: bool, matching_keywords_count: int | None = None, max_matching_keyword_weight: int | None = None, workload_available: int | None = None, workload_cap: int | None = None) -> AutomaticAssignmentAudit`. Task 5 relies on the fact that `dispatch_assignment` writes exactly one audit row whenever it returns an assignment.

- [ ] **Step 1: Write the failing assertions**

In `test_automatic_article_assignment.py`, change the models import to `from ..models import ArticleWorkflow, AutomaticAssignmentAudit, Reminder, WjsEditorAssignment`. Then append the following blocks at the **end** of the existing tests. Each test's setup is already validated.

At the end of `test_default_assign_editors_prefers_keyword_match_over_workload`:

```python
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == low_workload_with_match.user, "audit must name the chosen editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT, "default algorithm"
    assert audit.matched_by_keyword is True, "won via keyword ranking"
    assert audit.matching_keywords_count == 1, "one shared keyword"
    assert audit.max_matching_keyword_weight == 1, "StaffKeyword weight was 1"
    assert audit.workload_cap == 1, "monthly cap is min(workload=1, brake_on unset)"
    assert audit.workload_available == 1, "no assignments yet this month"
```

In `test_default_assign_editors_falls_back_to_workload_when_no_keyword_match`, replace `expected_editor = _selected_editor(parameters, journal=article.journal)` with:

```python
    expected_parameter = get_selected_editor_by_workload(parameters, journal=article.journal)
    expected_editor = expected_parameter.user
```

and append at the end:

```python
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == expected_editor, "audit must name the chosen editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT, "default algorithm"
    assert audit.matched_by_keyword is False, "fallback path does no keyword matching"
    assert audit.matching_keywords_count is None, "keyword fields stay empty on fallback"
    assert audit.max_matching_keyword_weight is None, "keyword fields stay empty on fallback"
    assert audit.workload_cap == expected_parameter.workload, "fallback cap is plain workload"
    assert audit.workload_available == expected_parameter.available_workload, "open-articles metric"
```

At the end of `test_jcom_assign_editors_ignores_keywords`:

```python
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == main_director.janeway_account, "audit must name the chosen director"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.JCOM, "JCOM algorithm"
    assert audit.matched_by_keyword is False, "JCOM never matches keywords"
    assert audit.workload_cap == 10, "director workload was set to 10"
    assert audit.workload_available == 10, "director had no open assignments"
```

At the end of `test_default_special_issue_assignment_ignores_keywords`:

```python
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == high_workload_no_match, "audit must name the chosen managing editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT_SPECIAL_ISSUE, "special-issue branch"
    assert audit.matched_by_keyword is False, "special issues bypass keyword matching"
    assert audit.workload_cap == 10, "managing editor workload was set to 10"
    assert audit.workload_available == 10, "managing editor had no open assignments"
```

Add this new test after `test_default_special_issue_assignment_ignores_keywords`:

```python
@pytest.mark.django_db
def test_no_audit_when_no_editor_assigned(review_settings, admin, article, editors, coauthors_setting):
    """No audit row is written when no editor could be assigned at all."""
    article.primary_issue = None
    article.save()
    StaffWorkloadParameters.objects.filter(user__in=editors).update(enabled=False)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    assert not WjsEditorAssignment.objects.filter(article=article).exists(), "precondition: nobody assigned"
    assert not AutomaticAssignmentAudit.objects.filter(article=article).exists(), "no audit without assignment"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "prefers_keyword or falls_back or jcom_assign_editors_ignores or special_issue_assignment_ignores or no_audit_when"`
Expected: the four extended tests FAIL with `AutomaticAssignmentAudit.DoesNotExist`. `test_no_audit_when_no_editor_assigned` PASSES, which pins the negative case.

- [ ] **Step 3: Add the recording helper**

In `events/assignment.py`, add `from django.db import transaction` to the imports and add this function before `default_assign_editors_to_articles`:

```python
def record_automatic_assignment_audit(
    *,
    article: Article,
    editor: Account,
    algorithm: str,
    matched_by_keyword: bool,
    matching_keywords_count: int | None = None,
    max_matching_keyword_weight: int | None = None,
    workload_available: int | None = None,
    workload_cap: int | None = None,
):
    """
    Record why the automatic assignment picked ``editor`` for ``article``.

    :return: The created AutomaticAssignmentAudit row.
    :rtype: AutomaticAssignmentAudit
    """
    from ..models import AutomaticAssignmentAudit

    return AutomaticAssignmentAudit.objects.create(
        article=article,
        editor=editor,
        algorithm=algorithm,
        matched_by_keyword=matched_by_keyword,
        matching_keywords_count=matching_keywords_count,
        max_matching_keyword_weight=max_matching_keyword_weight,
        workload_available=workload_available,
        workload_cap=workload_cap,
    )
```

- [ ] **Step 4: Capture in `default_assign_editors_to_articles`**

Replace everything from `if parameters:` to the end of the function with:

```python
    if parameters:
        from ..models import AutomaticAssignmentAudit

        request = get_current_request()
        user = None
        audit_data = {}
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
                audit_data = {
                    "algorithm": AutomaticAssignmentAudit.Algorithms.DEFAULT,
                    "matched_by_keyword": True,
                    "matching_keywords_count": matched_parameter.matching_keywords_count,
                    "max_matching_keyword_weight": matched_parameter.max_matching_keyword_weight,
                    "workload_available": matched_parameter.automatic_available_workload,
                    "workload_cap": matched_parameter.effective_workload_cap,
                }
        if user is None:
            # Fallback: caps on open-articles-needing-an-editor (no monthly window), unlike the
            # keyword-matched path above. See the docstring note for details.
            parameter = get_selected_editor_by_workload(parameters, journal=article.journal)
            if parameter:
                user = parameter.user
                audit_data = {
                    "algorithm": (
                        AutomaticAssignmentAudit.Algorithms.DEFAULT_SPECIAL_ISSUE
                        if is_special_issue
                        else AutomaticAssignmentAudit.Algorithms.DEFAULT
                    ),
                    "matched_by_keyword": False,
                    "workload_available": parameter.available_workload,
                    "workload_cap": parameter.workload,
                }
        if user:
            with transaction.atomic():
                assignment = BaseAssignToEditor(
                    editor=user, article=article, request=request, first_assignment=True
                ).run()
                record_automatic_assignment_audit(article=article, editor=user, **audit_data)
            return assignment
```

- [ ] **Step 5: Capture in `jcom_assign_editors_to_articles`**

Replace everything from `if parameters:` to the end of the function with:

```python
    if parameters:
        from ..models import AutomaticAssignmentAudit

        request = get_current_request()
        parameter = get_selected_editor_by_workload(parameters, journal=article.journal)
        if parameter:
            with transaction.atomic():
                assignment = BaseAssignToEditor(
                    editor=parameter.user, article=article, request=request, first_assignment=True
                ).run()
                record_automatic_assignment_audit(
                    article=article,
                    editor=parameter.user,
                    algorithm=AutomaticAssignmentAudit.Algorithms.JCOM,
                    matched_by_keyword=False,
                    workload_available=parameter.available_workload,
                    workload_cap=parameter.workload,
                )
            return assignment
```

- [ ] **Step 6: Run the tests**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
Expected: all PASS. If the JCOM or special-issue `workload_available == 10` assertion fails, check whether a fixture leaves an open assignment on that editor. If one does, assert against `get_selected_editor_by_workload(...)` computed before submission, the same way the fallback test does. Don't loosen the assertion into a range.

- [ ] **Step 7: Commit**

```bash
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat(wjs_review): record an audit row for every automatic editor assignment"
```

---

### Task 4: Audit page for EO/directors

**Files:**
- Modify: `wjs/plugins/wjs_review/filters.py`
- Modify: `wjs/plugins/wjs_review/views.py`
- Modify: `wjs/plugins/wjs_review/urls.py`
- Create: `wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/audit_list.html`
- Test: `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py`

**Interfaces:**
- Consumes: `AutomaticAssignmentAudit.objects.for_journal(journal)` (Task 2).
- Produces:
  - URL name `wjs-automatic-assignment-audit` (no args)
  - `AutomaticAssignmentAuditFilter` with the fields `editor`, `algorithm`, `matched_by_keyword`, `from_date` and `to_date`
  - `AutomaticAssignmentAuditList` view

The page is always scoped to `request.journal`: Janeway serves one journal per request, so the "journal" filter from the spec is applied implicitly in the queryset instead of being a form field.

- [ ] **Step 1: Write the failing tests**

Append to `test_automatic_assignment_audit.py`, and extend its imports with `from django.test import Client` and `from django.urls import reverse`:

```python
@pytest.mark.django_db
def test_audit_page_forbidden_to_section_editor(article, editors, section_editor):
    """Plain section editors cannot see the audit page."""
    client = Client()
    client.force_login(section_editor)

    response = client.get(reverse("wjs-automatic-assignment-audit"))

    assert response.status_code == 403, "only EO/directors may see the audit"


@pytest.mark.django_db
def test_audit_page_lists_own_journal_rows(article, editors, eo_user, journal_factory):
    """EO sees audit rows of the current journal only, newest first."""
    other_journal = journal_factory("JOTHER")
    other_article = Article.objects.create(journal=other_journal, title="Other", owner=editors[0])
    older = _make_audit(article, editors[0])
    newer = _make_audit(article, editors[1], matched_by_keyword=True, matching_keywords_count=2)
    _make_audit(other_article, editors[0])
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit"))

    assert response.status_code == 200, "EO may see the audit"
    assert list(response.context["audits"]) == [newer, older], "own journal only, newest first"


@pytest.mark.django_db
def test_audit_page_filters_by_matched_by_keyword(article, editors, director):
    """The matched-by-keyword filter narrows the list (directors may use the page too)."""
    _make_audit(article, editors[0])
    keyword_match = _make_audit(article, editors[1], matched_by_keyword=True)
    client = Client()
    client.force_login(director)

    response = client.get(reverse("wjs-automatic-assignment-audit"), {"matched_by_keyword": "true"})

    assert response.status_code == 200, "directors may see the audit"
    assert list(response.context["audits"]) == [keyword_match], "only keyword-matched rows"


@pytest.mark.django_db
def test_audit_page_filters_by_editor(article, editors, eo_user):
    """The editor filter narrows the list to the chosen editor."""
    _make_audit(article, editors[0])
    wanted = _make_audit(article, editors[1])
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit"), {"editor": editors[1].pk})

    assert list(response.context["audits"]) == [wanted], "only the chosen editor's rows"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py -k audit_page`
Expected: FAIL with `NoReverseMatch: Reverse for 'wjs-automatic-assignment-audit' not found`.

- [ ] **Step 3: Add the filterset**

In `filters.py`, extend the models import to `from .models import ArticleWorkflow, AutomaticAssignmentAudit, Message, Reminder` and append:

```python
class AutomaticAssignmentAuditFilter(django_filters.FilterSet):
    """Filters for the automatic-assignment audit page."""

    editor = django_filters.ModelChoiceFilter(
        label=_("Editor"),
        empty_label=_("All editors"),
        queryset=Account.objects.none(),
    )
    algorithm = django_filters.ChoiceFilter(
        label=_("Algorithm"),
        empty_label=_("All algorithms"),
        choices=AutomaticAssignmentAudit.Algorithms.choices,
    )
    matched_by_keyword = django_filters.BooleanFilter(label=_("Matched by keyword"))
    from_date = django_filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__gte",
        label=_("From date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    to_date = django_filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__lte",
        label=_("To date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model = AutomaticAssignmentAudit
        fields = ["editor", "algorithm", "matched_by_keyword"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters["editor"].queryset = Account.objects.filter(
            pk__in=self.queryset.values_list("editor", flat=True)
        ).order_by("last_name", "first_name")
```

- [ ] **Step 4: Add the view**

In `views.py`, add `AutomaticAssignmentAuditFilter` to the `from .filters import (...)` block and `AutomaticAssignmentAudit` to the `from .models import (...)` block. Add `"wjs-automatic-assignment-audit": _("Automatic assignment audit"),` as the last entry of both the `constants.EO_GROUP` and `constants.DIRECTOR_ROLE` dicts in `BaseRelatedViewsMixin.related_views`. Then add, right after `MessagesOverview`:

```python
class AutomaticAssignmentAuditList(BaseRelatedViewsMixin, FilterView):
    """List why each automatic editor assignment picked its editor, for EO/directors."""

    title = _("Automatic assignment audit")
    model = AutomaticAssignmentAudit
    template_name = "wjs_review/automatic_assignment_audit/audit_list.html"
    context_object_name = "audits"
    filterset_class = AutomaticAssignmentAuditFilter
    paginate_by = 50

    def test_func(self):
        """Allow access only to EO or directors."""
        return base_permissions.has_eo_or_director_role(self.request.journal, self.request.user)

    def get_queryset(self):
        """Return the current journal's audit rows, newest first."""
        return (
            AutomaticAssignmentAudit.objects.for_journal(self.request.journal)
            .select_related("article__articleworkflow", "editor")
            .order_by("-created_at", "-pk")
        )
```

- [ ] **Step 5: Add the URL**

In `urls.py`, add `AutomaticAssignmentAuditList` to the views import block and add this entry right after the `wjs_messages_overview` path:

```python
    path(
        "automatic-assignment-audit/",
        AutomaticAssignmentAuditList.as_view(),
        name="wjs-automatic-assignment-audit",
    ),
```

- [ ] **Step 6: Add the template**

Create `templates/wjs_review/automatic_assignment_audit/audit_list.html`:

```django
{% extends "wjs_review/base/base.html" %}
{% load django_bootstrap5 i18n %}

{% block content_wrapper %}
  <main class="container mb-3">
    <h1 class="fw-bold fs-3 mb-3">{{ view.title }}</h1>
    {% translate "Filter" as filter_label %}
    <form method="get" class="row g-2 align-items-end mb-3">
      {% bootstrap_field filter.form.editor wrapper_class="col-md-3" %}
      {% bootstrap_field filter.form.algorithm wrapper_class="col-md-2" %}
      {% bootstrap_field filter.form.matched_by_keyword wrapper_class="col-md-2" %}
      {% bootstrap_field filter.form.from_date wrapper_class="col-md-2" %}
      {% bootstrap_field filter.form.to_date wrapper_class="col-md-2" %}
      <div class="col-md-1">
        {% bootstrap_button button_type="submit" content=filter_label %}
      </div>
    </form>
    <div class="table-responsive">
      <table class="table table-sm table-striped align-middle">
        <thead>
          <tr>
            <th scope="col">{% translate "Date" %}</th>
            <th scope="col">{% translate "Article" %}</th>
            <th scope="col">{% translate "Editor" %}</th>
            <th scope="col">{% translate "Algorithm" %}</th>
            <th scope="col">{% translate "Matched by keyword" %}</th>
            <th scope="col">{% translate "Matching keywords" %}</th>
            <th scope="col">{% translate "Max keyword weight" %}</th>
            <th scope="col">{% translate "Available workload" %}</th>
            <th scope="col">{% translate "Workload cap" %}</th>
          </tr>
        </thead>
        <tbody>
          {% for audit in audits %}
            <tr>
              <td>{{ audit.created_at|date:"Y-m-d H:i" }}</td>
              <td>
                <a href="{% url "wjs_article_details" pk=audit.article.articleworkflow.pk %}">{{ audit.article.pk }} - {{ audit.article.title|truncatechars:60 }}</a>
              </td>
              <td>{{ audit.editor.full_name|default:"—" }}</td>
              <td>{{ audit.get_algorithm_display }}</td>
              <td>{{ audit.matched_by_keyword|yesno }}</td>
              <td>{{ audit.matching_keywords_count|default_if_none:"—" }}</td>
              <td>{{ audit.max_matching_keyword_weight|default_if_none:"—" }}</td>
              <td>{{ audit.workload_available|default_if_none:"—" }}</td>
              <td>{{ audit.workload_cap|default_if_none:"—" }}</td>
            </tr>
          {% empty %}
            <tr>
              <td colspan="9">{% translate "No automatic assignments recorded." %}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% if is_paginated %}
      {% bootstrap_pagination page_obj extra=request.GET.urlencode %}
    {% endif %}
  </main>
{% endblock content_wrapper %}
```

Note: `audit.editor.full_name` resolves Janeway's `Account.full_name()` method in the template. `bootstrap_pagination` with `extra` keeps the filter querystring across pages.

- [ ] **Step 7: Run the tests and djlint**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py`
Expected: all PASS.

Run: `cd /home/yakky/Projects/sissa/wjs-profile-project && djlint --profile=django wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add wjs/plugins/wjs_review/filters.py wjs/plugins/wjs_review/views.py wjs/plugins/wjs_review/urls.py \
  wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/ \
  wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py
git commit -m "feat(wjs_review): add automatic assignment audit page for EO and directors"
```

---

### Task 5: Replay business logic (reset, snapshot, chronological replay)

**Files:**
- Create: `wjs/plugins/wjs_review/logic__replay.py`
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (upper time bound in `annotate_automatic_assignment_workload`)
- Modify: `setup.cfg` (add `freezegun` to `install_requires`)
- Test: `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py` (new)
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` (time-bound test)

**Interfaces:**
- Consumes: `dispatch_assignment(article) -> WjsEditorAssignment | None` (unchanged), `AutomaticAssignmentAudit.objects.latest_for_article(article)` (Task 2), `create_rich_fake_request(journal, settings, user)` (`wjs/jcom_profile/utils.py:284`), and `get_system_user(journal)` (`communication_utils.py:162`).
- Produces (Task 6 uses these):
  - `get_original_editor(article: Article) -> Account | None`: the **first** editor ever assigned (earliest across `PastEditorAssignment.date_assigned` and `WjsEditorAssignment.assigned`)
  - `ResetArticleForReplay(article: Article).run() -> None`, which raises `ValidationError` for articles that reached typesetting
  - `ReplayReportRow` dataclass with the fields `article: Article`, `original_editor: Account | None`, `new_editor: Account | None`, `audit: AutomaticAssignmentAudit | None` and `skipped_reason: str = ""`, and the property `match -> bool`
  - `ReplayAutomaticAssignment(articles: QuerySet[Article]).run() -> list[ReplayReportRow]`
  - `get_replayable_articles(journal: Journal, date_from: date | None = None, date_to: date | None = None, article_ids: list[int] | None = None) -> QuerySet[Article]`

- [ ] **Step 1: Write the failing test for the monthly time bound**

Append to `test_automatic_article_assignment.py` (add `import freezegun` to its imports):

```python
@pytest.mark.django_db
def test_annotate_automatic_assignment_workload_ignores_future_assignments(article, editors, journal):
    """
    Assignments dated after "now" must not count against this month's cap.

    Never happens live, but the historical replay freezes "now" at each article's submission date while
    later assignments (from articles outside the replayed batch) already exist in the database.
    """
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    parameters.workload = 1
    parameters.brake_on = 0
    parameters.save()
    future_assignment = WjsEditorAssignment.objects.create(
        article=article, editor=editors[0], editor_type="section-editor"
    )
    # Freeze on the 10th and date the assignment on the 20th: same month, but after "now".
    frozen_now = timezone.now().replace(day=10, hour=12, minute=0, second=0, microsecond=0)
    WjsEditorAssignment.objects.filter(pk=future_assignment.pk).update(assigned=frozen_now.replace(day=20))

    with freezegun.freeze_time(frozen_now):
        annotated = annotate_automatic_assignment_workload(
            StaffWorkloadParameters.objects.filter(pk=parameters.pk)
        ).get()

    assert annotated.assignment_count == 0, "assignments after 'now' must not be counted"
```

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k ignores_future_assignments`
Expected: FAIL with `assignment_count == 1`, or `DoesNotExist` because the editor looks exhausted.

- [ ] **Step 2: Add the upper bound**

In `annotate_automatic_assignment_workload`, replace:

```python
    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assigned_this_month = (
        WjsEditorAssignment.objects.filter(
            editor=OuterRef("user_id"),
            article__journal=OuterRef("journal_id"),
            assigned__gte=month_start,
        )
```

with:

```python
    now = timezone.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assigned_this_month = (
        WjsEditorAssignment.objects.filter(
            editor=OuterRef("user_id"),
            article__journal=OuterRef("journal_id"),
            assigned__gte=month_start,
            # Upper bound is a no-op live; it keeps the historical replay (which freezes "now") honest.
            assigned__lte=now,
        )
```

Also change the docstring sentence "Only WjsEditorAssignment rows assigned in the current calendar month count against the cap" to "Only WjsEditorAssignment rows assigned in the current calendar month, up to now, count against the cap".

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k annotate_automatic_assignment_workload`
Expected: all PASS.

- [ ] **Step 3: Declare freezegun as a runtime dependency**

In `setup.cfg`, under `install_requires`, add `freezegun` after `croniter ~= 2.0.0`.

- [ ] **Step 4: Write the failing replay tests**

Create `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`:

```python
"""Tests for the historical replay of automatic editor assignment."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from review.models import ReviewAssignment, ReviewRound
from submission.models import STAGE_UNASSIGNED, Article

from wjs.jcom_profile.models import StaffWorkloadParameters

from ..logic import BaseAssignToEditor
from ..logic__replay import (
    ReplayAutomaticAssignment,
    ResetArticleForReplay,
    get_original_editor,
    get_replayable_articles,
)
from ..models import (
    ArticleWorkflow,
    AutomaticAssignmentAudit,
    EditorDecision,
    PastEditorAssignment,
    WjsEditorAssignment,
)
from .conftest import _accept_article

DEFAULT_ASSIGNMENT = {None: "plugins.wjs_review.events.assignment.default_assign_editors_to_articles"}


def _submitted(articles, start):
    """Mark articles as submitted one day apart from ``start``, in list order."""
    for offset, article in enumerate(articles):
        article.date_submitted = start + timedelta(days=offset)
        article.primary_issue = None
        article.save()
        article.articleworkflow.state = ArticleWorkflow.ReviewStates.SUBMITTED
        article.articleworkflow.save()
    return articles


@pytest.mark.django_db
def test_get_original_editor_current_assignment_only(assigned_article):
    """With no reassignment, the current WjsEditorAssignment's editor is the first editor."""
    expected = WjsEditorAssignment.objects.get_current(assigned_article).editor

    assert get_original_editor(assigned_article) == expected, "sole assignment is the first one"


@pytest.mark.django_db
def test_get_original_editor_returns_first_editor_after_reassignment(assigned_article, editors):
    """After a reassignment, the first (past) editor is returned, not the current one."""
    current = WjsEditorAssignment.objects.get_current(assigned_article)
    first_editor = editors[0]
    PastEditorAssignment.objects.create(
        article=assigned_article,
        editor=first_editor,
        date_assigned=current.assigned - timedelta(days=10),
        date_unassigned=current.assigned - timedelta(days=1),
    )
    # A later past assignment must not win either.
    PastEditorAssignment.objects.create(
        article=assigned_article,
        editor=editors[1],
        date_assigned=current.assigned - timedelta(days=1),
        date_unassigned=current.assigned,
    )

    assert get_original_editor(assigned_article) == first_editor, "earliest assignment wins"


@pytest.mark.django_db
def test_get_original_editor_none_without_assignments(article):
    """An article that was never assigned has no original editor."""
    assert get_original_editor(article) is None, "no assignment, no original editor"


@pytest.mark.django_db
def test_reset_clears_review_state(fake_request, assigned_article_with_reviewer):
    """Reset deletes editor assignments, review assignments, rounds and decisions, and rewinds the workflow."""
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article_with_reviewer).editor
    article = _accept_article(fake_request, assigned_article_with_reviewer)
    assert EditorDecision.objects.filter(workflow=article.articleworkflow).exists(), "precondition: decision"

    ResetArticleForReplay(article=article).run()
    article.refresh_from_db()

    assert not WjsEditorAssignment.objects.filter(article=article).exists(), "editor assignments deleted"
    assert not ReviewAssignment.objects.filter(article=article).exists(), "review assignments deleted"
    assert not ReviewRound.objects.filter(article=article).exists(), "review rounds deleted"
    assert not EditorDecision.objects.filter(workflow=article.articleworkflow).exists(), "decisions deleted"
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.SUBMITTED, "workflow rewound"
    assert article.stage == STAGE_UNASSIGNED, "article stage rewound"


@pytest.mark.django_db
def test_reset_refuses_typeset_articles(assigned_to_typesetter_article):
    """Articles that reached typesetting are refused and left untouched."""
    state_before = assigned_to_typesetter_article.articleworkflow.state

    with pytest.raises(ValidationError):
        ResetArticleForReplay(article=assigned_to_typesetter_article).run()

    assigned_to_typesetter_article.articleworkflow.refresh_from_db()
    assert assigned_to_typesetter_article.articleworkflow.state == state_before, "state untouched"
    assert WjsEditorAssignment.objects.filter(article=assigned_to_typesetter_article).exists(), "not deleted"


@pytest.mark.django_db
def test_replay_accumulates_workload_in_submission_order(
    review_settings, journal, editors, create_submitted_articles, fake_request
):
    """
    Articles are replayed by date_submitted and each replayed assignment counts for the next one.

    All three articles were historically assigned to editors[2]. The batch is reset up front, so those
    historical assignments must not count against editors[2] when the first article is replayed. With
    equal workload of 1 each and the fallback (no keyword) path, each editor must get exactly one article,
    and the earliest-submitted article goes to the lowest-id parameter (tie-break).
    """
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=1, brake_on=0)
    first_param = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).order_by("id").first()
    articles = create_submitted_articles(journal, 3)
    # The highest pk gets the earliest submission date, to prove ordering is by date_submitted, not pk.
    earliest, middle, latest = _submitted(list(reversed(articles)), timezone.now() - timedelta(days=10))
    for article in articles:
        BaseAssignToEditor(editor=editors[2], article=article, request=fake_request, first_assignment=True).run()

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        rows = ReplayAutomaticAssignment(articles=Article.objects.filter(pk__in=[a.pk for a in articles])).run()

    assert [row.article for row in rows] == [earliest, middle, latest], "processed in date_submitted order"
    assert {row.new_editor for row in rows} == set(editors), "workload accumulates: one article per editor"
    assert rows[0].new_editor == first_param.user, "tie on available workload is broken by parameter id"
    assert all(row.original_editor == editors[2] for row in rows), "original editor snapshotted before reset"
    for row in rows:
        assignment = WjsEditorAssignment.objects.get(article=row.article)
        assert assignment.assigned == row.article.date_submitted, "assignment dated at the simulated submission"
        assert row.audit == AutomaticAssignmentAudit.objects.latest_for_article(row.article), "audit linked"
        assert row.audit.created_at == row.article.date_submitted, "audit dated at the simulated submission"
        row.article.articleworkflow.refresh_from_db()
        assert row.article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED, "like live"


@pytest.mark.django_db
def test_replay_reports_unassigned_article(review_settings, journal, editors, create_submitted_articles):
    """When no editor is available, the row has no new editor, no audit, and the workflow awaits a director."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(enabled=False)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert row.new_editor is None, "nobody available"
    assert row.audit is None, "no audit without assignment"
    assert row.match is False, "no match without a new editor"
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED, "like live"


@pytest.mark.django_db
def test_replay_skips_typeset_articles(review_settings, assigned_to_typesetter_article):
    """Typeset articles are reported as skipped and left untouched; the batch does not abort."""
    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(
            articles=Article.objects.filter(pk=assigned_to_typesetter_article.pk)
        ).run()

    assert row.skipped_reason, "a skip reason must be reported"
    assert row.new_editor is None, "skipped articles are not replayed"
    assert WjsEditorAssignment.objects.filter(article=assigned_to_typesetter_article).exists(), "untouched"


@pytest.mark.django_db
def test_replay_sends_no_real_email(review_settings, journal, editors, create_submitted_articles, settings, mocker):
    """Replay forces the locmem email backend, whatever the instance is configured with."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    smtp_send = mocker.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=0)
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        (row,) = ReplayAutomaticAssignment(articles=Article.objects.filter(pk=article.pk)).run()

    assert row.new_editor is not None, "precondition: an assignment (and its notification) happened"
    smtp_send.assert_not_called()


@pytest.mark.django_db
def test_get_replayable_articles_filters(journal, create_submitted_articles):
    """Only submitted articles of the journal in the date window (or explicit ids) are selected."""
    inside, outside, incomplete = create_submitted_articles(journal, 3)
    now = timezone.now()
    _submitted([inside], now - timedelta(days=5))
    _submitted([outside], now - timedelta(days=50))
    incomplete.date_submitted = None
    incomplete.save()

    by_window = get_replayable_articles(journal, date_from=(now - timedelta(days=10)).date(), date_to=now.date())
    by_ids = get_replayable_articles(journal, article_ids=[outside.pk, incomplete.pk])

    assert list(by_window) == [inside], "date window on date_submitted"
    assert list(by_ids) == [outside], "explicit ids, still excluding never-submitted articles"
```

- [ ] **Step 5: Run the tests to verify they fail**

Run test: `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named '...logic__replay'`.

- [ ] **Step 6: Implement `logic__replay.py`**

Create `wjs/plugins/wjs_review/logic__replay.py`:

```python
"""
Historical replay of the automatic editor assignment.

Only meant for an imported copy of the production database: resetting articles is destructive by design.
See docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md.
"""

import dataclasses
from datetime import date

import freezegun
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.test import override_settings
from django.utils.translation import gettext_lazy as _
from journal.models import Journal
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from submission.models import STAGE_UNASSIGNED, Article
from typesetting.models import TypesettingAssignment

from wjs.jcom_profile.utils import create_rich_fake_request

from .communication_utils import get_system_user
from .events.assignment import dispatch_assignment
from .models import (
    ArticleWorkflow,
    AutomaticAssignmentAudit,
    EditorDecision,
    EditorRevisionRequest,
    PastEditorAssignment,
    WjsEditorAssignment,
)

Account = get_user_model()


def get_original_editor(article: Article) -> Account | None:
    """
    Return the first editor ever assigned to the article.

    That is the editor the automatic assignment (or the director, if it found nobody) originally picked;
    later reassignments are not what the replay is compared against. Reassigned editors live in
    PastEditorAssignment, the current one in WjsEditorAssignment: the earliest across both wins.
    """
    candidates = []
    past = PastEditorAssignment.objects.filter(article=article).order_by("date_assigned", "pk").first()
    if past:
        candidates.append((past.date_assigned, past.editor))
    current = WjsEditorAssignment.objects.filter(article=article).order_by("assigned", "pk").first()
    if current:
        candidates.append((current.assigned, current.editor))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


def get_replayable_articles(
    journal: Journal,
    date_from: date | None = None,
    date_to: date | None = None,
    article_ids: list[int] | None = None,
) -> QuerySet[Article]:
    """Return the journal's submitted articles to replay, oldest submission first."""
    articles = Article.objects.filter(
        journal=journal, date_submitted__isnull=False, articleworkflow__isnull=False
    ).exclude(articleworkflow__state=ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION)
    if article_ids:
        articles = articles.filter(pk__in=article_ids)
    if date_from:
        articles = articles.filter(date_submitted__date__gte=date_from)
    if date_to:
        articles = articles.filter(date_submitted__date__lte=date_to)
    return articles.order_by("date_submitted", "pk")


@dataclasses.dataclass
class ResetArticleForReplay:
    """Reset an article to "just submitted, never processed", destroying its editorial history."""

    article: Article

    def _check_conditions(self) -> None:
        if TypesettingAssignment.objects.filter(round__article=self.article).exists():
            raise ValidationError(_("Article reached typesetting: typesetting reset is not supported."))

    def run(self) -> None:
        """Delete assignments, review rounds and decisions, and rewind the workflow to SUBMITTED."""
        with transaction.atomic():
            self._check_conditions()
            # Order matters: decisions and revision requests PROTECT their review round.
            EditorDecision.objects.filter(workflow__article=self.article).delete()
            EditorRevisionRequest.objects.filter(article=self.article).delete()
            # ReviewAssignment.review_round is SET_NULL, so review assignments must be deleted explicitly.
            ReviewAssignment.objects.filter(article=self.article).delete()
            ReviewRound.objects.filter(article=self.article).delete()
            # Deleting the Janeway parent rows removes the WjsEditorAssignment children too.
            EditorAssignment.objects.filter(article=self.article).delete()
            Article.objects.filter(pk=self.article.pk).update(stage=STAGE_UNASSIGNED)
            ArticleWorkflow.objects.filter(article=self.article).update(state=ArticleWorkflow.ReviewStates.SUBMITTED)
            self.article.refresh_from_db()


@dataclasses.dataclass
class ReplayReportRow:
    """Outcome of replaying one article."""

    article: Article
    original_editor: Account | None
    new_editor: Account | None = None
    audit: AutomaticAssignmentAudit | None = None
    skipped_reason: str = ""

    @property
    def match(self) -> bool:
        """Tell whether the replay picked the historical editor."""
        return self.new_editor is not None and self.new_editor == self.original_editor


@dataclasses.dataclass
class ReplayAutomaticAssignment:
    """
    Replay the automatic assignment of a batch of articles, in submission order.

    The whole batch is snapshotted and reset before any article is replayed: otherwise the historical
    assignments of not-yet-replayed articles would still count against their editors' workload.
    Each article is then replayed with "now" frozen at its submission date, so both the monthly window and
    the new assignment's date match what happened historically, and workload accumulates across the batch.
    """

    articles: QuerySet[Article]

    def _reset_all(self, articles: list[Article]) -> tuple[list[ReplayReportRow], list[ReplayReportRow]]:
        replayable, skipped = [], []
        for article in articles:
            row = ReplayReportRow(article=article, original_editor=get_original_editor(article))
            try:
                ResetArticleForReplay(article=article).run()
            except ValidationError as e:
                row.skipped_reason = " ".join(e.messages)
                skipped.append(row)
            else:
                replayable.append(row)
        return replayable, skipped

    def _replay(self, row: ReplayReportRow) -> None:
        article = row.article
        with freezegun.freeze_time(article.date_submitted):
            create_rich_fake_request(article.journal, settings, user=get_system_user(article.journal))
            assignment = dispatch_assignment(article=article)
            # Mirror models.process_submission, which moves the workflow once assignment is attempted.
            ArticleWorkflow.objects.filter(article=article).update(
                state=(
                    ArticleWorkflow.ReviewStates.EDITOR_SELECTED
                    if assignment
                    else ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED
                )
            )
        if assignment:
            row.new_editor = assignment.editor
            row.audit = AutomaticAssignmentAudit.objects.latest_for_article(article)

    def run(self) -> list[ReplayReportRow]:
        """Reset and replay the batch; return one report row per article, in submission order."""
        articles = list(self.articles.order_by("date_submitted", "pk"))
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            with transaction.atomic():
                replayable, skipped = self._reset_all(articles)
                for row in replayable:
                    self._replay(row)
        order = {article.pk: index for index, article in enumerate(articles)}
        return sorted(replayable + skipped, key=lambda row: order[row.article.pk])
```

Implementation notes for the engineer:
- The `typesetting` import path matches `forms__production.py:11` (`from typesetting.models import ...`).
- `create_rich_fake_request` registers the fake request with `GlobalRequestMiddleware`, which is what `get_current_request()` inside `default_assign_editors_to_articles` reads. Without it, `BaseAssignToEditor` gets `request=None`.
- The whole batch runs in one transaction on purpose. On a copy instance, a half-reset database is worse than a failed run, so an unexpected exception rolls everything back.
- Don't call `workflow.system_process_submission()`. It would also run `dispatch_checks` and `dispatch_eo_assignment`, and the spec requires calling `dispatch_assignment` directly.

- [ ] **Step 7: Run the replay tests**

Run test: `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`
Expected: all PASS.

If `test_reset_clears_review_state` fails on a `ProtectedError`, some other model protects `ReviewRound` or the `Article`. Read the error's model name, add an explicit delete for that model *before* `ReviewRound` in `ResetArticleForReplay.run`, and re-run. Don't switch to raw SQL.

- [ ] **Step 8: Commit**

```bash
git add setup.cfg wjs/plugins/wjs_review/logic__replay.py wjs/plugins/wjs_review/events/assignment.py \
  wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py \
  wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit -m "feat(wjs_review): add historical replay logic for automatic editor assignment"
```

---

### Task 6: `replay_automatic_assignment` management command and CSV report

**Files:**
- Create: `wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py`
- Test: `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`

**Interfaces:**
- Consumes: `get_replayable_articles`, `ReplayAutomaticAssignment` and `ReplayReportRow` (Task 5).
- Produces: `manage.py replay_automatic_assignment --journal CODE (--date-from YYYY-MM-DD --date-to YYYY-MM-DD | --article-ids ID [ID ...]) [--output-file PATH] --confirm-copy-instance`. It writes a CSV with the columns in `REPORT_FIELDS` and prints a summary.

CSV columns: the spec's list plus a trailing `skipped_reason`, so that articles skipped at the typesetting stage stay visible in the report instead of silently disappearing.

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_automatic_assignment.py`, and add `import csv`, `from django.core.management import call_command` and `from django.core.management.base import CommandError` to the imports:

```python
@pytest.mark.django_db
def test_command_refuses_without_confirmation(journal):
    """Without --confirm-copy-instance the command refuses to touch anything."""
    with pytest.raises(CommandError, match="confirm-copy-instance"):
        call_command("replay_automatic_assignment", "--journal", journal.code, "--article-ids", "1")


@pytest.mark.django_db
def test_command_requires_selection(journal):
    """Either a date window or explicit article ids must be given."""
    with pytest.raises(CommandError, match="--article-ids"):
        call_command("replay_automatic_assignment", "--journal", journal.code, "--confirm-copy-instance")


@pytest.mark.django_db
def test_command_writes_csv_report(review_settings, journal, editors, create_submitted_articles, tmp_path, capsys):
    """The command writes one CSV row per article with the spec's columns and prints a summary."""
    StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal).update(workload=5)
    (article,) = _submitted(create_submitted_articles(journal, 1), timezone.now() - timedelta(days=3))
    output = tmp_path / "report.csv"

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=DEFAULT_ASSIGNMENT):
        call_command(
            "replay_automatic_assignment",
            "--journal",
            journal.code,
            "--article-ids",
            str(article.pk),
            "--output-file",
            str(output),
            "--confirm-copy-instance",
        )

    with output.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == [
        "article_id",
        "title",
        "journal",
        "date_submitted",
        "original_editor",
        "new_editor",
        "match",
        "algorithm",
        "matched_by_keyword",
        "matching_keywords_count",
        "max_matching_keyword_weight",
        "workload_available",
        "workload_cap",
        "skipped_reason",
    ], "CSV columns must follow the spec (plus skipped_reason)"
    assert len(rows) == 1, "one row per replayed article"
    assert rows[0]["article_id"] == str(article.pk), "row describes the replayed article"
    assert rows[0]["algorithm"] == AutomaticAssignmentAudit.Algorithms.DEFAULT, "audit fields are included"
    assert rows[0]["match"] == "False", "never-assigned article cannot match"
    assert "Processed: 1" in capsys.readouterr().out, "summary printed to stdout"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run test: `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py -k command`
Expected: FAIL with `CommandError: Unknown command: 'replay_automatic_assignment'`.

- [ ] **Step 3: Implement the command**

Create `wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py`:

```python
"""Replay the automatic editor assignment of historical articles and report original vs new editor."""

import csv
from collections import Counter
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from journal.models import Journal

from ...logic__replay import ReplayAutomaticAssignment, ReplayReportRow, get_replayable_articles

REPORT_FIELDS = [
    "article_id",
    "title",
    "journal",
    "date_submitted",
    "original_editor",
    "new_editor",
    "match",
    "algorithm",
    "matched_by_keyword",
    "matching_keywords_count",
    "max_matching_keyword_weight",
    "workload_available",
    "workload_cap",
    "skipped_reason",
]


def _account_label(account) -> str:
    return f"{account.full_name()} <{account.email}>" if account else ""


def report_row_to_dict(row: ReplayReportRow) -> dict:
    """Flatten a replay report row into a CSV record."""
    audit = row.audit
    return {
        "article_id": row.article.pk,
        "title": row.article.title,
        "journal": row.article.journal.code,
        "date_submitted": row.article.date_submitted.isoformat(),
        "original_editor": _account_label(row.original_editor),
        "new_editor": _account_label(row.new_editor),
        "match": row.match,
        "algorithm": audit.algorithm if audit else "",
        "matched_by_keyword": audit.matched_by_keyword if audit else "",
        "matching_keywords_count": audit.matching_keywords_count if audit else "",
        "max_matching_keyword_weight": audit.max_matching_keyword_weight if audit else "",
        "workload_available": audit.workload_available if audit else "",
        "workload_cap": audit.workload_cap if audit else "",
        "skipped_reason": row.skipped_reason,
    }


class Command(BaseCommand):
    help = (  # noqa: A003
        "DESTRUCTIVE - run only on an imported copy of production. Reset the selected articles and replay "
        "their automatic editor assignment in submission order, writing an original-vs-new CSV report."
    )

    def add_arguments(self, parser):
        parser.add_argument("--journal", required=True, help="Journal code")
        parser.add_argument("--date-from", type=date.fromisoformat, help="First submission date (YYYY-MM-DD)")
        parser.add_argument("--date-to", type=date.fromisoformat, help="Last submission date (YYYY-MM-DD)")
        parser.add_argument("--article-ids", type=int, nargs="+", help="Explicit article ids to replay")
        parser.add_argument("--output-file", help="CSV report path (default: timestamped file in cwd)")
        parser.add_argument(
            "--confirm-copy-instance",
            action="store_true",
            help="Confirm this is a disposable copy of production: articles WILL be reset",
        )

    def handle(self, *args, **options):
        if not options["confirm_copy_instance"]:
            raise CommandError("Refusing to run: pass --confirm-copy-instance (copy instances only).")
        if not options["article_ids"] and not (options["date_from"] and options["date_to"]):
            raise CommandError("Pass either --article-ids or both --date-from and --date-to.")
        try:
            journal = Journal.objects.get(code=options["journal"])
        except Journal.DoesNotExist as e:
            raise CommandError(f"Journal '{options['journal']}' does not exist") from e

        articles = get_replayable_articles(
            journal,
            date_from=options["date_from"],
            date_to=options["date_to"],
            article_ids=options["article_ids"],
        )
        rows = ReplayAutomaticAssignment(articles=articles).run()

        output_file = options["output_file"] or f"replay_automatic_assignment_{timezone.now():%Y%m%d_%H%M%S}.csv"
        with open(output_file, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(report_row_to_dict(row) for row in rows)

        outcome = Counter(
            "skipped" if row.skipped_reason else "unassigned" if row.new_editor is None else row.match for row in rows
        )
        self.stdout.write(f"Processed: {len(rows)}")
        self.stdout.write(f"Matching original editor: {outcome[True]}")
        self.stdout.write(f"Different editor: {outcome[False]}")
        self.stdout.write(f"No editor found: {outcome['unassigned']}")
        self.stdout.write(f"Skipped: {outcome['skipped']}")
        self.stdout.write(f"Report written to {output_file}")
```

- [ ] **Step 4: Run the command tests**

Run test: `wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py \
  wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py
git commit -m "feat(wjs_review): add replay_automatic_assignment management command"
```

---

### Task 7: Docs, lint and regression pass

**Files:**
- Modify: `wjs/plugins/wjs_review/CLAUDE.md`

- [ ] **Step 1: Document the feature**

Append this section to `wjs/plugins/wjs_review/CLAUDE.md` (read the file first and match its heading level):

```markdown
## Automatic assignment audit & replay

- Every automatic editor assignment (`default_assign_editors_to_articles`, `jcom_assign_editors_to_articles` in
  `events/assignment.py`) writes one append-only `AutomaticAssignmentAudit` row via
  `record_automatic_assignment_audit`, in the same transaction as `BaseAssignToEditor.run()`. The row is not linked
  to `WjsEditorAssignment` (which is hard-deleted on reassignment). `workload_available`/`workload_cap` hold
  whichever metric actually ran: monthly cap for keyword matches, open-articles metric otherwise.
- EO/directors browse it at `wjs-automatic-assignment-audit` (`AutomaticAssignmentAuditList`).
- `manage.py replay_automatic_assignment` (logic in `logic__replay.py`) is DESTRUCTIVE and meant only for an
  imported copy of production: it resets the selected articles, replays them by `date_submitted` under
  `freezegun`, and writes a CSV of original vs new editor. Articles that reached typesetting are skipped.
  Known limitation: the fallback (open-articles) metric sees today's states of articles outside the batch.
```

- [ ] **Step 2: Run pre-commit**

Run: `cd /home/yakky/Projects/sissa/wjs-profile-project && pre-commit run --all-files`
Expected: all hooks pass. If black or isort rewrite files, re-stage them. Fix any pydocstyle complaints by editing the docstrings, not by adding ignores.

- [ ] **Step 3: Run the affected test modules**

Run test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py -n4`
Expected: all PASS.

Then run the modules most likely to be hit by the `related_views` change and the new migration:
Run test: `wjs/plugins/wjs_review/tests/test_views.py wjs/plugins/wjs_review/tests/test_submission.py -n7`
Expected: all PASS.

- [ ] **Step 4: Migration sanity check**

Run: `cd /home/yakky/Projects/sissa/janeway/src && python manage.py makemigrations wjs_review --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 5: Commit**

```bash
git add wjs/plugins/wjs_review/CLAUDE.md
git commit -m "docs(wjs_review): document automatic assignment audit and replay command"
```
