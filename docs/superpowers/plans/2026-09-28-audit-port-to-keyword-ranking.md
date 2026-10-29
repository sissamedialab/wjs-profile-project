# Port the Automatic-Assignment Audit and Replay onto the Rewritten Keyword Ranking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring !1534 (`feature/issue-3136-automatic-assignment-audit`) up to date with the rewritten editor-ranking pipeline on `feature/automatic-assignment-keyword-matching` (!1512), so that the audit trail and the historical replay record and exercise the ranking that actually runs.

**Architecture:** First merge the new keyword branch into !1513's design branch, then merge that branch into ours, so !1534's diff stays limited to our own work. The two conflicting files are resolved by taking the keyword branch's version and re-applying our additions:
- the audit capture moves onto `annotate_keyword_ranking` → `annotate_workload_availability` → `rank_editor_parameters`;
- `matched_by_keyword` becomes "the winner shares at least one keyword";
- a new audit field records the 30-day availability, which is now the workload tie-break;
- the replay's "no assignments after now" bound moves into `annotate_workload_availability`, covering both of its windows.

**Tech Stack:** Django 5.2 ORM, django-fsm-2, freezegun, pytest + pytest-django, existing `wjs_review` fixtures.

**Spec:** `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md` (rev 1.1; bumped to 1.2 in Task 4), together with the keyword branch's own design `docs/superpowers/specs/2026-09-22-editor-assignment-review-findings-design.md` (it arrives with the merge). Background: `docs/superpowers/plans/2026-09-26-automatic-assignment-audit-HANDOVER.md`.

## Global Constraints

- **The keyword branch's pipeline is authoritative.** Don't change `annotate_keyword_ranking`, `rank_editor_parameters` or the ranking order (`max_matching_keyword_weight` desc nulls last, `-matching_keywords_count`, `-available_workload_30d`, `id`). Only additive changes are allowed to `annotate_workload_availability`: the upper time bound in Task 3.
- **Keep the owner decisions** from the audit spec rev 1.1:
    - the audit is append-only, with no FK to `WjsEditorAssignment`;
    - an audit row is written only after `BaseAssignToEditor(...).run()` succeeds, in the same `transaction.atomic()`;
    - `algorithm` is `default` / `default_special_issue` / `jcom`;
    - the replay's "original editor" is the first editor ever assigned;
    - the replay reset puts the workflow back to `SUBMITTED`.
- **`matched_by_keyword`** = the winner's `matching_keywords_count > 0`. `matching_keywords_count` and `max_matching_keyword_weight` stay `None` when there is no match, as the spec says ("set only when `matched_by_keyword=True`").
- **Workload fields** on the default (non-special-issue) path:
    - `workload_available` = `available_workload_month`, the metric that excludes editors;
    - `workload_cap` = `effective_workload_cap`;
    - the new `workload_available_30d` = `available_workload_30d`, the metric that breaks ties.

  Special-issue and JCOM paths keep `get_selected_editor_by_workload`'s `available_workload` / `workload`, with `workload_available_30d=None`.
- **`get_selected_editor_by_workload`** keeps our return type (the annotated `StaffWorkloadParameters` row, or `None`). The keyword branch left it unchanged, so there's no semantic conflict.
- **Don't edit migration `0018_automaticassignmentaudit`.** The local `janeway` DB already has it applied. The new field goes in `0019`.
- **Don't reintroduce `filter_parameters_by_matching_keywords` or `annotate_automatic_assignment_workload`**, and don't port our zero-keyword fix. The new ranking never excludes, and the article-without-keywords case is covered by a behaviour test in Task 1.
- Style: black 119, double quotes, docstrings on public functions, `gettext_lazy` for user-facing strings, `verbose_name` on model fields, templates with 2-space indentation and named endblocks.
- **Tests:** `cd /home/yakky/Projects/sissa/janeway/src && pytest ../../wjs-profile-project/<path> -q -n4`. Add `--create-db` on the first run after a model change.
    - Before running anything, check `readlink /home/yakky/Projects/sissa/janeway/src/plugins/wjs_review` points at `/home/yakky/Projects/sissa/wjs-profile-project/wjs/plugins/wjs_review`. If it doesn't, stop and ask.
    - Don't run the full suite except in Task 4.
- **Git:**
    - work in place on `feature/issue-3136-automatic-assignment-audit`, no worktrees;
    - commit with `git commit --no-gpg-sign`, ending the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu`;
    - **never push without asking the user**. That includes the design branch in Task 0.

## Review Focus

1. **An article with no keywords** must still be assigned by workload, and its audit row must say `matched_by_keyword=False` with empty keyword fields. The old code crashed into the wrong path here. The test is in Task 1.
2. **Every editor over their monthly cap:** there's no assignment and no audit row, and the article is left in `EDITOR_TO_BE_SELECTED`. The test is in Task 1.
3. **An editor disabled with `brake_on = -1`** must never appear as the chosen editor in an audit row. The test is in Task 1.
4. **A frozen-time replay** must not count assignments dated after the simulated "now", in either the monthly or the 30-day window. The test is in Task 3.
5. **Audit rows written before this change** have `workload_available_30d = NULL`. The audit page and the replay CSV must render them (as "—" and as an empty cell) without errors. The test is in Task 2.

---

## File Structure

- **Task 0 (git only):** merge `origin/feature/automatic-assignment-keyword-matching` into `docs/automatic-assignment-audit-design`, then that branch into ours.
- **Modify:** `wjs/plugins/wjs_review/events/assignment.py`, where the merge is resolved. This covers the capture on the new pipeline (Task 1), the 30d field (Task 2) and the time bound (Task 3).
- **Modify:** `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`, where the merge is resolved and our tests are re-applied (Tasks 1–3).
- **Modify:** `wjs/plugins/wjs_review/models.py`. **Create:** `wjs/plugins/wjs_review/migrations/0019_automaticassignmentaudit_workload_available_30d.py`. Both in Task 2.
- **Modify:** `record_automatic_assignment_audit`, `templates/wjs_review/automatic_assignment_audit/audit_list.html`, `management/commands/replay_automatic_assignment.py`, `tests/test_automatic_assignment_audit.py` and `tests/test_replay_automatic_assignment.py`. All in Task 2.
- **Modify (docs, Task 4):**
    - `wjs/plugins/wjs_review/CLAUDE.md`;
    - `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md`, bumped to rev 1.2;
    - `docs/superpowers/plans/2026-09-26-automatic-assignment-audit-HANDOVER.md`.

---

### Task 0: Bring the keyword branch into the design branch and ours

**Files:** no code; git only.

**Interfaces:**
- Produces: the local branch `docs/automatic-assignment-audit-design` containing `a99e180c`, and our branch mid-merge with conflicts in `events/assignment.py` and `tests/test_automatic_article_assignment.py`. The conflicts are resolved by Task 1, which finishes the merge commit.

- [ ] **Step 1: Update the design branch**

```bash
cd /home/yakky/Projects/sissa/wjs-profile-project
git fetch origin feature/automatic-assignment-keyword-matching docs/automatic-assignment-audit-design
git switch docs/automatic-assignment-audit-design
git merge --ff-only origin/docs/automatic-assignment-audit-design
git merge --no-gpg-sign --no-edit origin/feature/automatic-assignment-keyword-matching
git log --oneline -1
```

Expected: a clean merge commit. The design branch only adds docs on top of the old keyword branch, so there's nothing to conflict. If it conflicts, run `git merge --abort` and stop: report the conflicted paths.

- [ ] **Step 2: Ask before pushing the design branch**

!1513 is a shared MR, so stop and ask the user whether to push `docs/automatic-assignment-audit-design`. Don't push it yourself without a yes. The rest of the plan works on local branches either way.

- [ ] **Step 3: Start the merge into our branch**

```bash
git switch feature/issue-3136-automatic-assignment-audit
git merge --no-gpg-sign --no-commit docs/automatic-assignment-audit-design
git status --short | grep -E "^(UU|AA|DU|UD)"
```

Expected: exactly two conflicted files, `wjs/plugins/wjs_review/events/assignment.py` and `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`. `views.py` auto-merges. If any other file conflicts, stop and report it.

Keep this reference open for Task 1: `git diff 3a6293c8 HEAD -- wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`. It's everything our branch changed in the two files since the common ancestor, and it's the list of things to re-apply.

---

### Task 1: Resolve the merge — capture audit rows on the new pipeline

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py`
- Modify: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Consumes (from the keyword branch):
    - `annotate_keyword_ranking(article, users_parameters)`, which annotates `matching_keywords_count` (int, 0 if no match) and `max_matching_keyword_weight` (int or `None`);
    - `annotate_workload_availability(users_parameters)`, which annotates `assignment_count_month`, `assignment_count_30d`, `effective_workload_cap`, `available_workload_month` and `available_workload_30d`;
    - `rank_editor_parameters(users_parameters)`.
- Keeps (from our branch):
    - `get_selected_editor_by_workload(users_parameters, journal) -> StaffWorkloadParameters | None`;
    - `record_automatic_assignment_audit(*, article, editor, algorithm, matched_by_keyword, matching_keywords_count=None, max_matching_keyword_weight=None, workload_available=None, workload_cap=None) -> "AutomaticAssignmentAudit"`.
- Produces: `dispatch_assignment` writes exactly one audit row per assignment on every path. Task 2 extends the recorder.

- [ ] **Step 1: Take the keyword branch's side of both files**

```bash
git checkout --theirs wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
```

Both files are now exactly the keyword branch's versions. The next steps re-apply our additions on top.

- [ ] **Step 2: Re-apply our additions to `events/assignment.py`**

1. Add `from django.db import transaction` to the imports.
2. Replace the `TYPE_CHECKING` import with:

```python
if TYPE_CHECKING:
    from ..models import (  # noqa
        ArticleWorkflow,
        AutomaticAssignmentAudit,
        WjsEditorAssignment,
    )
```

3. Make `get_selected_editor_by_workload` return the row. Its signature becomes `users_parameters: QuerySet[StaffWorkloadParameters], journal: Journal) -> StaffWorkloadParameters | None`. Its `:return:` doc becomes "The winning StaffWorkloadParameters, annotated with ``assignment_count`` and ``available_workload`` (so callers can both read ``.user`` and record the numbers the choice was based on); None if no user is available." Its body ends with `return annotated_parameters.order_by("-available_workload", "id").first()`. Copy it from our side of the reference diff.
4. Insert `record_automatic_assignment_audit` right before `default_assign_editors_to_articles`, copied verbatim from our side of the reference diff.
5. In `default_assign_editors_to_articles`, keep the keyword branch's docstring and replace everything from `if parameters:` to the end of the function with:

```python
    if parameters:
        from ..models import AutomaticAssignmentAudit

        request = get_current_request()
        user = None
        audit_data = {}
        if is_special_issue:
            parameter = get_selected_editor_by_workload(parameters, journal=article.journal)
            if parameter:
                user = parameter.user
                audit_data = {
                    "algorithm": AutomaticAssignmentAudit.Algorithms.DEFAULT_SPECIAL_ISSUE,
                    "matched_by_keyword": False,
                    "workload_available": parameter.available_workload,
                    "workload_cap": parameter.workload,
                }
        else:
            ranked_parameters = rank_editor_parameters(
                annotate_workload_availability(annotate_keyword_ranking(article, parameters)).filter(
                    available_workload_month__gt=0,
                ),
            )
            if matched_parameter := ranked_parameters.first():
                user = matched_parameter.user
                matched_by_keyword = matched_parameter.matching_keywords_count > 0
                audit_data = {
                    "algorithm": AutomaticAssignmentAudit.Algorithms.DEFAULT,
                    "matched_by_keyword": matched_by_keyword,
                    "matching_keywords_count": (
                        matched_parameter.matching_keywords_count if matched_by_keyword else None
                    ),
                    "max_matching_keyword_weight": matched_parameter.max_matching_keyword_weight,
                    "workload_available": matched_parameter.available_workload_month,
                    "workload_cap": matched_parameter.effective_workload_cap,
                }
        if user:
            with transaction.atomic():
                assignment = BaseAssignToEditor(
                    editor=user, article=article, request=request, first_assignment=True
                ).run()
                record_automatic_assignment_audit(article=article, editor=user, **audit_data)
            return assignment
```

6. In `jcom_assign_editors_to_articles`, replace everything from `if parameters:` to the end of the function with our side's version: the `parameter = get_selected_editor_by_workload(...)` branch, `algorithm=JCOM`, inside `transaction.atomic()`. Copy it from the reference diff. Leave the `managing_editors` truthiness line exactly as the keyword branch has it: its fix is a separate MR, !1535.

Check nothing old survived:
`grep -n "filter_parameters_by_matching_keywords\|annotate_automatic_assignment_workload\|<<<<<<<\|>>>>>>>" wjs/plugins/wjs_review/events/assignment.py`
Expected: no output.

- [ ] **Step 3: Re-apply our test additions to `test_automatic_article_assignment.py`**

These re-apply what our branch already had, adapted to the new functions:
1. Add `AutomaticAssignmentAudit` to the `from ..models import (...)` line, and `import freezegun` to the imports (Task 3 uses it).
2. Add the `_selected_editor(parameters, journal)` helper after `RANDOM_WJS_ARTICLE_ASSIGNMENT_FUNCTIONS`, verbatim from our side. Then replace every direct `get_selected_editor_by_workload(parameters, journal=...)` call used as an `Account` with `_selected_editor(...)`. That covers the tests at the keyword branch's lines ~92, 341, 392, 445, 577–584, 777 and 832, the same call sites as our side.
3. Re-add our two tests `test_get_selected_editor_by_workload_returns_annotated_parameter` and `test_get_selected_editor_by_workload_returns_none_without_candidates`, verbatim from our side, after `test_get_selected_editor_by_workload_include_by_state`.
4. Re-add our audit blocks at the end of `test_jcom_assign_editors_ignores_keywords` and `test_default_special_issue_assignment_ignores_keywords`, and re-add `test_no_audit_when_no_editor_assigned`, all verbatim from our side.
5. **Don't** re-add any `test_filter_parameters_by_matching_keywords_*` or `test_annotate_automatic_assignment_workload_*` test. The keyword branch's `test_annotate_keyword_ranking_*` and `test_annotate_workload_availability_*` replace them. The future-bound test comes back in Task 3.

Check the file only uses functions that exist:
`grep -n "filter_parameters_by_matching_keywords\|annotate_automatic_assignment_workload\|<<<<<<<\|>>>>>>>" wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`
Expected: no output.

- [ ] **Step 4: Write the new audit assertions (they fail until Step 2's capture is correct)**

At the end of `test_default_assign_editors_prefers_keyword_match_over_workload`, append:

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

In `test_default_assign_editors_picks_best_workload_when_no_keyword_match`, replace the `expected_editor = (...)` assignment with:

```python
    expected_parameter = (
        rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters)))
        .filter(available_workload_month__gt=0)
        .first()
    )
    expected_editor = expected_parameter.user
```

and append at the end:

```python
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == expected_editor, "audit must name the chosen editor"
    assert audit.algorithm == AutomaticAssignmentAudit.Algorithms.DEFAULT, "same default path, no fallback branch"
    assert audit.matched_by_keyword is False, "no candidate shares a keyword"
    assert audit.matching_keywords_count is None, "keyword fields stay empty without a match"
    assert audit.max_matching_keyword_weight is None, "keyword fields stay empty without a match"
    assert audit.workload_available == expected_parameter.available_workload_month, "monthly availability"
    assert audit.workload_cap == expected_parameter.effective_workload_cap, "effective monthly cap"
```

At the end of `test_default_assign_editors_no_assignment_when_all_editors_over_monthly_cap`, append:

```python
    assert not AutomaticAssignmentAudit.objects.filter(article=article).exists(), "no audit without assignment"
```

At the end of `test_default_assign_editors_excludes_eo_disabled_editor`, append:

```python
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.editor == available_editor, "a brake_on=-1 editor must never be the audited choice"
```

Add this new test right after `test_default_assign_editors_picks_best_workload_when_no_keyword_match` (Review Focus 1):

```python
@pytest.mark.django_db
def test_default_assign_editors_article_without_keywords_is_assigned_by_workload(
    review_settings, admin, article, editors, keywords, coauthors_setting
):
    """An article with no keywords is still assigned, and its audit row records no keyword match."""
    article.primary_issue = None
    article.save()
    article.keywords.clear()
    for parameters in StaffWorkloadParameters.objects.filter(user__in=editors):
        StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        url = reverse("submit_review", args=(article.pk,))
        response = client.post(url, data={"next_step": "next_step"})
        assert response.status_code == 302

    assert WjsEditorAssignment.objects.filter(article=article).exists(), "article must still get an editor"
    audit = AutomaticAssignmentAudit.objects.get(article=article)
    assert audit.matched_by_keyword is False, "an article without keywords cannot match"
    assert audit.matching_keywords_count is None, "keyword fields stay empty without a match"
    assert audit.max_matching_keyword_weight is None, "keyword fields stay empty without a match"
```

- [ ] **Step 5: Run the module**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py --create-db`.
Expected: all pass. If an assertion from Step 4 fails, fix `default_assign_editors_to_articles`, not the test. Report any test you think is wrong instead of changing its expectation.

- [ ] **Step 6: Run the other affected modules**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py wjs/plugins/wjs_review/tests/test_views.py`.
Expected: all pass. The replay tests don't mention the removed functions. `test_views.py` covers the keyword branch's manual-selection changes, which merged cleanly.

- [ ] **Step 7: Conclude the merge**

```bash
pre-commit run --files wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit --no-gpg-sign -m "merge: port automatic-assignment audit onto the rewritten keyword ranking

Merge docs/automatic-assignment-audit-design (now carrying the rewritten
feature/automatic-assignment-keyword-matching). Audit capture moves onto
annotate_keyword_ranking / annotate_workload_availability /
rank_editor_parameters; matched_by_keyword now means the winner shares a
keyword. The zero-keyword fix is dropped: the new ranking never excludes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 2: Record the 30-day availability that breaks ties

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (`AutomaticAssignmentAudit`)
- Create: `wjs/plugins/wjs_review/migrations/0019_automaticassignmentaudit_workload_available_30d.py` (generated)
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (`record_automatic_assignment_audit`, the default path's `audit_data`)
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/audit_list.html`
- Modify: `wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py`
- Test: `tests/test_automatic_article_assignment.py`, `tests/test_automatic_assignment_audit.py`, `tests/test_replay_automatic_assignment.py`

**Interfaces:**
- Produces:
    - `AutomaticAssignmentAudit.workload_available_30d: int | None`;
    - `record_automatic_assignment_audit(..., workload_available_30d: int | None = None)`;
    - the CSV column `workload_available_30d`, placed right after `workload_available`.

- [ ] **Step 1: Write the failing tests**

In `test_automatic_article_assignment.py`, append to `test_default_assign_editors_prefers_keyword_match_over_workload`:

```python
    assert audit.workload_available_30d == 1, "trailing-30-day availability, the ranking tie-break"
```

and to `test_default_assign_editors_picks_best_workload_when_no_keyword_match`:

```python
    assert audit.workload_available_30d == expected_parameter.available_workload_30d, "30-day tie-break metric"
```

and to `test_default_special_issue_assignment_ignores_keywords`:

```python
    assert audit.workload_available_30d is None, "special issues don't use the 30-day metric"
```

In `test_automatic_assignment_audit.py`, append this test (Review Focus 5):

```python
@pytest.mark.django_db
def test_audit_page_renders_rows_without_30d_value(article, editors, eo_user):
    """Rows written before the 30-day field existed (NULL) render as a dash, not an error."""
    _make_audit(article, editors[0], workload_available_30d=None)
    client = Client()
    client.force_login(eo_user)

    response = client.get(reverse("wjs-automatic-assignment-audit"))

    assert response.status_code == 200, "page must render legacy rows"
    assert "30-day" in response.content.decode(), "the new column header must be shown"
```

In `test_replay_automatic_assignment.py`, in `test_command_writes_csv_report`, insert `"workload_available_30d",` into the expected column list right after `"workload_available",`.

- [ ] **Step 2: Run the tests to verify they fail**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py -k "prefers_keyword or picks_best_workload or special_issue_assignment_ignores or without_30d or csv_report"`.
Expected: FAIL. You should see `AttributeError`/`FieldError` on `workload_available_30d`, and a CSV column-list mismatch.

- [ ] **Step 3: Add the field and its migration**

In `models.py`, add this right after `workload_cap` in `AutomaticAssignmentAudit`:

```python
    workload_available_30d = models.IntegerField(_("Available workload (last 30 days)"), null=True, blank=True)
```

Extend the model docstring's last sentence with: "``workload_available_30d`` holds the trailing-30-day availability that breaks ranking ties on the default path (empty elsewhere)."

Generate the migration:

```bash
cd /home/yakky/Projects/sissa/janeway/src && python manage.py makemigrations wjs_review -n automaticassignmentaudit_workload_available_30d
```

Expected: `0019_automaticassignmentaudit_workload_available_30d.py` with a single `AddField`, depending on `0018_automaticassignmentaudit`. If `makemigrations` also creates a `submission/migrations/*.py` file inside the Janeway checkout, delete it: it's spurious translation-field noise. Then `python manage.py makemigrations wjs_review --check --dry-run` → `No changes detected`.

- [ ] **Step 4: Record the value**

In `record_automatic_assignment_audit`, add the keyword parameter `workload_available_30d: int | None = None` after `workload_cap`, and pass `workload_available_30d=workload_available_30d` to `create(...)`. In `default_assign_editors_to_articles`, add `"workload_available_30d": matched_parameter.available_workload_30d,` to the non-special-issue `audit_data` dict.

- [ ] **Step 5: Show it on the page and in the CSV**

In `audit_list.html`, add a header cell after `{% translate "Workload cap" %}`:

```django
            <th scope="col">{% translate "Available (30-day)" %}</th>
```

Add a body cell after the `workload_cap` cell:

```django
              <td>{{ audit.workload_available_30d|default_if_none:"—" }}</td>
```

Change the empty-row `colspan="9"` to `colspan="10"`.

In `replay_automatic_assignment.py`, insert `"workload_available_30d",` into `REPORT_FIELDS` after `"workload_available",`. Add `"workload_available_30d": audit.workload_available_30d if audit else "",` to `report_row_to_dict` after the `workload_available` entry.

Note: Step 1's page test checks for the text "30-day", which matches the header "Available (30-day)".

- [ ] **Step 6: Run the tests**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py --create-db`.
Expected: all pass.

Then run `cd /home/yakky/Projects/sissa/wjs-profile-project && djlint --profile=django wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/`. Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/migrations/0019_automaticassignmentaudit_workload_available_30d.py \
  wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/audit_list.html \
  wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py wjs/plugins/wjs_review/tests/
git commit --no-gpg-sign -m "feat(wjs_review): record the 30-day availability that breaks assignment ties

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 3: Keep the frozen-time replay honest in both workload windows

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (`annotate_workload_availability`)
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Produces: `annotate_workload_availability` counts only assignments with `assigned <= now` in both `assignment_count_month` and `assignment_count_30d`. It's a no-op in live use.

- [ ] **Step 1: Write the failing test** (Review Focus 4)

Append next to the other `test_annotate_workload_availability_*` tests:

```python
@pytest.mark.django_db
def test_annotate_workload_availability_ignores_future_assignments(article, editors, journal):
    """
    Assignments dated after "now" count in neither window.

    Never happens live, but the historical replay freezes "now" at each article's submission date while
    later assignments (from articles outside the replayed batch) already exist in the database.
    """
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    parameters.workload = 5
    parameters.brake_on = 0
    parameters.save()
    future_assignment = WjsEditorAssignment.objects.create(
        article=article, editor=editors[0], editor_type="section-editor"
    )
    # Freeze on the 10th and date the assignment on the 20th: inside both windows' lower bounds, but after "now".
    frozen_now = timezone.now().replace(day=10, hour=12, minute=0, second=0, microsecond=0)
    WjsEditorAssignment.objects.filter(pk=future_assignment.pk).update(assigned=frozen_now.replace(day=20))

    with freezegun.freeze_time(frozen_now):
        annotated = annotate_workload_availability(StaffWorkloadParameters.objects.filter(pk=parameters.pk)).get()

    assert annotated.assignment_count_month == 0, "monthly window must ignore assignments after 'now'"
    assert annotated.assignment_count_30d == 0, "30-day window must ignore assignments after 'now'"
```

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k ignores_future_assignments`.
Expected: FAIL with `assignment_count_month == 1`.

- [ ] **Step 2: Add the upper bound**

In `annotate_workload_availability`, replace:

```python
    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    window_30d = timezone.localtime() - datetime.timedelta(days=30)
```

with:

```python
    now = timezone.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    window_30d = now - datetime.timedelta(days=30)
```

Then add `assigned__lte=now,` after the `assigned__gte=...` line in **both** subqueries (`assigned_this_month` and `assigned_last_30d`), with the comment `# Upper bound is a no-op live; it keeps the historical replay (which freezes "now") honest.` above the first one. In the docstring, change "Both counts exclude WITHDRAWN articles." to "Both counts exclude WITHDRAWN articles and assignments dated after now (a no-op live; it matters for the frozen-time replay)."

- [ ] **Step 3: Run the tests**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "annotate_workload_availability or ignores_future" wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py`.
Expected: all pass, including the keyword branch's own window tests.

- [ ] **Step 4: Commit**

```bash
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit --no-gpg-sign -m "fix(wjs_review): ignore assignments after now in both workload windows

Keeps the frozen-time historical replay from counting later assignments.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 4: Docs, full suite, and a replay smoke run

**Files:**
- Modify: `wjs/plugins/wjs_review/CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md`
- Modify: `docs/superpowers/plans/2026-09-26-automatic-assignment-audit-HANDOVER.md`

- [ ] **Step 1: Update `wjs_review/CLAUDE.md`**

In the "Automatic assignment audit & replay" section, replace the sentence about which metric fills `workload_available`/`workload_cap` with:

"On the default path the audit records `matched_by_keyword` (the winner shares at least one keyword), the keyword count and weight when matched, `workload_available`/`workload_cap` from the monthly window (`available_workload_month`, which excludes over-cap editors) and `workload_available_30d` (the ranking tie-break). Special-issue and JCOM assignments record `get_selected_editor_by_workload`'s open-articles numbers instead."

- [ ] **Step 2: Bump the audit spec to rev 1.2**

- Add a row to the Revisions table: `| 1.2 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-28 | Capture points follow the rewritten keyword ranking (!1512): no separate fallback branch; matched_by_keyword = winner shares a keyword; new workload_available_30d field |`.
- In "Background: current automatic assignment flow", replace the `default_assign_editors_to_articles` bullet with one describing `annotate_keyword_ranking` → `annotate_workload_availability` (filtered on `available_workload_month > 0`) → `rank_editor_parameters`. Keyword matching ranks but never excludes, and special issues keep `get_selected_editor_by_workload`.
- In the Data model table, add a row: `workload_available_30d` | nullable `IntegerField` | trailing-30-day availability of the chosen editor (default path only; the ranking tie-break).
- In "Capture points", replace the keyword-matched and fallback bullets with a single default-path bullet matching Task 1's `audit_data`. Keep the special-issue and JCOM bullets.

- [ ] **Step 3: Update the handover**

In the handover's "Branches and MRs", mark !1534's port as done: "merged the rewritten keyword branch (via the design branch), capture moved onto the new pipeline, 30-day field added (migration 0019)". Remove item 1 from "Next steps". Add a line under "Findings" saying the 40-article analysis ran on the **old** ranking (monthly tie-break, keyword-excluding), and that a rerun is needed on the new one.

- [ ] **Step 4: Full suite**

Run: `cd /home/yakky/Projects/sissa/janeway/src && pytest ../../wjs-profile-project -q -n7 -m "not fix_labels"` (about 22 minutes; run it in the background).
Expected: no failures. Then run `cd /home/yakky/Projects/sissa/janeway/src && python manage.py makemigrations wjs_review --check --dry-run` → `No changes detected`. Then run `cd /home/yakky/Projects/sissa/wjs-profile-project && pre-commit run --all-files` → all hooks pass.

- [ ] **Step 5: Replay smoke run on the local copy (JCAP)**

Apply the new migration on the local `janeway` DB, then replay 5 fresh eligible JCAP articles. That means articles with a historical editor, no `TypesettingAssignment` and no replay fingerprint, picked as in the handover's Next steps:

```bash
cd /home/yakky/Projects/sissa/janeway/src && python manage.py migrate wjs_review 0019
python manage.py replay_automatic_assignment --journal JCAP --article-ids <5 ids> --output-file /tmp/claude-1002/-home-yakky-Projects-sissa-wjs-profile-project/dcb8fb89-24fb-4939-9aa0-3573cdf5385b/scratchpad/replay-smoke-new-pipeline.csv --confirm-copy-instance
```

Expected: exit 0, `Processed: 5`, and a CSV whose `workload_available_30d` column is filled for default-path rows. This is a smoke check of the port only: report the summary lines, don't analyse the results.

- [ ] **Step 6: Commit (don't push)**

```bash
git add wjs/plugins/wjs_review/CLAUDE.md docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md docs/superpowers/plans/2026-09-26-automatic-assignment-audit-HANDOVER.md
git commit --no-gpg-sign -m "docs: align audit spec, plugin docs and handover with the ported ranking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

Then ask the user whether to push `feature/issue-3136-automatic-assignment-audit`, and the design branch if Task 0 Step 2 didn't push it already.
