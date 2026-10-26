# Weighted Keyword Score for Editor Ranking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank candidate editors by one weighted keyword score that uses every shared keyword and both article and editor keyword weights. Use it in automatic assignment and on the manual "Select new editor" page, and report it in the automatic-assignment audit (record, audit page, replay CSV).

**Architecture:**
- **On !1512 (`feature/automatic-assignment-keyword-matching`):**
    - `get_editors_with_keywords` gets a `keyword_score` annotation, computed in a correlated subquery over `StaffKeyword` using `KeywordArticle.weight`.
    - `annotate_keyword_ranking` copies it onto the `StaffWorkloadParameters` rows.
    - `rank_editor_parameters` and the manual view order by it, then by `available_workload_30d`, then by `id`.
    - The manual table shows it.
- **On !1534 (`feature/issue-3136-automatic-assignment-audit`):** !1512 comes in through !1513's design branch. Then a `keyword_score` audit field (migration 0020) is recorded and shown on the audit page and in the replay CSV.

**Tech Stack:** Django 5.2 ORM (`Subquery`, `Case`/`When`, `Least`, `Sum`), pytest + pytest-django, existing `wjs_review` / `wjs.jcom_profile` fixtures.

**Spec:** `docs/superpowers/specs/2026-09-29-weighted-keyword-score-design.md` (committed on !1512, `8e626063`). Also `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md` (on !1534), which Task 6 bumps to rev 1.3.

## Global Constraints

- **Score formula:**
  `keyword_score = Σ_{k ∈ K(A) ∩ K(E)} w_A(k) · min(w_E(k), 100) / (Σ_{k ∈ K(A)} w_A(k) · 100)`
    - `w_A` is `KeywordArticle.weight`; **NULL counts as 100**.
    - `w_E` is `StaffKeyword.weight`; **values above 100 count as 100**.
    - The range is 0–1.
    - The score is **`None`** when there's no shared keyword or the article has no keywords.
    - Only editor keywords from the editor's `StaffWorkloadParameters` for the **article's journal** count.
- **The score must be computed in a correlated subquery**, never as a `Sum` in `get_editors_with_keywords`' main query. That query joins past editor assignments and would multiply the sum.
- **Ranking order**, for both automatic assignment and the manual page:
    1. `F("keyword_score").desc(nulls_last=True)`
    2. `F("available_workload_30d").desc(nulls_last=True)`
    3. `"id"`

  `num_shared_kwds` / `matching_keywords_count` and `max_matching_keyword_weight` keep being annotated, but no longer order anything.
- **Not changed:**
    - workload rules (`available_workload_month > 0` exclusion, `brake_on = -1`, 30-day availability);
    - special-issue and JCOM paths;
    - `get_editors_with_keywords`' own default `order_by("-num_shared_kwds", ...)` for other callers;
    - the manual page's non-excluding behaviour.
- **Display:** a whole percentage (e.g. `52%`), with "—" when `None`.
    - Manual page header: `{% translate "Keyword score" %}`.
    - Audit page header: `{% translate "Keyword score" %}`.
    - CSV column `keyword_score`: 3 decimals, empty when `None`, placed right after `max_matching_keyword_weight`.
- **Audit field:** `AutomaticAssignmentAudit.keyword_score = models.FloatField(_("Keyword score"), null=True, blank=True)`, in migration `wjs_review.0020`.
    - The default path stores the winner's score.
    - Special-issue and JCOM leave it `None`.
- **Style:**
    - black 119 columns, double quotes;
    - docstrings on every public function (imperative, ending with a period);
    - `gettext_lazy` for user-facing strings, `verbose_name` on model fields;
    - templates use 2-space indentation and named endblocks;
    - every test assertion has a message.
- **Tests:**
    - Command: `cd /home/yakky/Projects/sissa/janeway/src && pytest ../../wjs-profile-project/<path> -q -n4`. Add `--create-db` after model changes.
    - The Janeway env tests **whatever branch is checked out in the main checkout**. Check `readlink /home/yakky/Projects/sissa/janeway/src/plugins/wjs_review` is `/home/yakky/Projects/sissa/wjs-profile-project/wjs/plugins/wjs_review` before running.
- **Git:**
    - work in place, no worktrees;
    - commit with `git commit --no-gpg-sign`, ending the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu`;
    - **never push without asking the user** (!1512 and !1513 are shared MRs).

## Review Focus

1. **Past assignments must not inflate the score.** An editor who was a past editor of the same article must get the same score as one who wasn't. This guards against join multiplication. Test is in Task 1.
2. **Editor keywords from another journal must not count** towards the score for this journal's article. Test is in Task 1.
3. **Article keywords with a NULL weight** (non-JCAP journals, or keywords added via `article.keywords.add`) count as 100, not 0 and not a crash. Test is in Task 1.
4. **A non-matching editor (score `None`) must rank last,** never first. Postgres sorts NULLs first under DESC, so this applies to automatic assignment and to the manual page. Tests are in Task 2.
5. **Audit rows written before this change** (NULL `keyword_score`) must render as "—" on the audit page and as an empty cell in the CSV. Test is in Task 6.

---

## File Structure

On !1512 (Tasks 1–4):
- **Modify** `wjs/plugins/wjs_review/users.py`: `get_editors_with_keywords` (the `keyword_score` annotation).
- **Modify** `wjs/plugins/wjs_review/events/assignment.py`: `annotate_keyword_ranking` (carries `keyword_score`) and `rank_editor_parameters` (new order).
- **Modify** `wjs/plugins/wjs_review/views.py`: `SupervisorAssignEditor._editors_with_keywords` ordering.
- **Modify** `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html` and `editor_list.html`: the score column.
- **Tests:** `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py` and `wjs/plugins/wjs_review/tests/test_views.py`.
- **Docs:** `wjs/plugins/wjs_review/CLAUDE.md`; create `docs/superpowers/specs/2026-09-29-weighted-keyword-score-wiki-text.md` (the text for the wiki).

On !1513 → !1534 (Tasks 5–6):
- Task 5 is git only: merge !1512 into `docs/automatic-assignment-audit-design`, then that branch into `feature/issue-3136-automatic-assignment-audit`.
- **Modify** `wjs/plugins/wjs_review/models.py`. **Create** migration `0020_automaticassignmentaudit_keyword_score.py`.
- **Modify** `events/assignment.py` (the recorder and the default-path `audit_data`), `templates/wjs_review/automatic_assignment_audit/audit_list.html` and `management/commands/replay_automatic_assignment.py`.
- **Tests:** `tests/test_automatic_article_assignment.py`, `tests/test_automatic_assignment_audit.py`, `tests/test_replay_automatic_assignment.py`.
- **Docs:** the audit spec (rev 1.3) and `wjs/plugins/wjs_review/CLAUDE.md`.

Task 7 is the acceptance replay on the local DB. It is controller-run and needs a fresh DB import by the user.

---

### Task 1: Compute `keyword_score` (on !1512)

**Files:**
- Modify: `wjs/plugins/wjs_review/users.py` (`get_editors_with_keywords`)
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (`annotate_keyword_ranking`)
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`

**Interfaces:**
- Produces:
    - `Account.objects.get_editors_with_keywords(article, ...)` rows gain `keyword_score: float | None`.
    - `annotate_keyword_ranking(article, users_parameters)` rows gain `keyword_score: float | None`.

- [ ] **Step 0: Check the branch**

```bash
cd /home/yakky/Projects/sissa/wjs-profile-project && git branch --show-current
```

Expected: `feature/automatic-assignment-keyword-matching`. If it isn't, stop and report.

- [ ] **Step 1: Write the failing tests**

Add `KeywordArticle` and `PastEditorAssignment` to the imports of `test_automatic_article_assignment.py`: `from submission.models import Article, KeywordArticle` and `PastEditorAssignment` from `..models`. Merge them into the existing import lines rather than duplicating them. Then append:

```python
def _set_article_keyword_weights(article, weights_by_keyword):
    """Attach keywords to the article with the given KeywordArticle weights (None allowed)."""
    for keyword, weight in weights_by_keyword.items():
        article.keywords.add(keyword)
        KeywordArticle.objects.filter(article=article, keyword=keyword).update(weight=weight)


def _score_for(article, editor):
    """Return the keyword_score of one editor for the article."""
    return Account.objects.get_editors_with_keywords(article, include_matching_keywords=False).get(pk=editor.pk).keyword_score


@pytest.mark.django_db
def test_keyword_score_matches_worked_example(article, editors, keywords, journal):
    """Spec example: article weights 100/75/50/25, editor shares k0 (80) and k2 (100) -> 13000 / 25000 = 0.52."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 75, keywords[2]: 50, keywords[3]: 25})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=80)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[2], weight=100)

    assert _score_for(article, editors[0]) == pytest.approx(0.52), "weighted coverage of the article's keywords"


@pytest.mark.django_db
def test_keyword_score_caps_editor_weight_at_100(article, editors, keywords, journal):
    """An editor weight of 999 counts as 100, so full coverage scores exactly 1."""
    _set_article_keyword_weights(article, {keywords[0]: 100})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=999)

    assert _score_for(article, editors[0]) == pytest.approx(1.0), "editor weights above 100 are capped"


@pytest.mark.django_db
def test_keyword_score_null_article_weight_counts_as_100(article, editors, keywords, journal):
    """A NULL KeywordArticle.weight counts as 100: (100*100) / ((100 + 50) * 100) = 2/3."""
    _set_article_keyword_weights(article, {keywords[0]: None, keywords[1]: 50})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)

    assert _score_for(article, editors[0]) == pytest.approx(2 / 3), "NULL article weight counts as 100"


@pytest.mark.django_db
def test_keyword_score_is_none_without_shared_keywords(article, editors, keywords, journal):
    """No shared keyword, or an article without keywords, gives None rather than 0."""
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[5], weight=100)
    article.keywords.clear()

    assert _score_for(article, editors[0]) is None, "article without keywords has no score"

    _set_article_keyword_weights(article, {keywords[0]: 100})

    assert _score_for(article, editors[0]) is None, "editor without a shared keyword has no score"


@pytest.mark.django_db
def test_keyword_score_ignores_other_journal_keywords(article, editors, keywords, journal, journal_factory):
    """Keywords the editor holds only in another journal's parameters don't count."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)
    other_parameters = StaffWorkloadParameters.objects.create(user=editors[0], journal=journal_factory("JOTHER"))
    StaffKeyword.objects.create(parameters=other_parameters, keyword=keywords[1], weight=100)

    assert _score_for(article, editors[0]) == pytest.approx(0.5), "only this journal's editor keywords count"


@pytest.mark.django_db
def test_keyword_score_not_inflated_by_past_assignments(article, editors, keywords, journal):
    """Being a past editor of the article must not multiply the score (join-multiplication guard)."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.get(user=editors[0], journal=journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=keywords[0], weight=100)
    before = _score_for(article, editors[0])
    now = timezone.now()
    for days in (10, 5):
        PastEditorAssignment.objects.create(
            article=article,
            editor=editors[0],
            date_assigned=now - timedelta(days=days + 1),
            date_unassigned=now - timedelta(days=days),
        )

    assert before == pytest.approx(0.5), "precondition: half coverage"
    assert _score_for(article, editors[0]) == pytest.approx(before), "past assignments must not change the score"


@pytest.mark.django_db
def test_annotate_keyword_ranking_carries_keyword_score(article, editors, keywords, journal):
    """annotate_keyword_ranking exposes keyword_score on the StaffWorkloadParameters rows."""
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    matched, unmatched, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=matched, keyword=keywords[0], weight=100)

    result = {p.pk: p for p in annotate_keyword_ranking(article, parameters)}

    assert result[matched.pk].keyword_score == pytest.approx(0.5), "score carried onto parameters"
    assert result[unmatched.pk].keyword_score is None, "no match keeps None"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "keyword_score"`.
Expected: FAIL with `AttributeError: ... has no attribute 'keyword_score'`.

- [ ] **Step 3: Implement the score in `get_editors_with_keywords`**

In `wjs/plugins/wjs_review/users.py`, add these imports if missing:
- `from django.db.models import Case, ExpressionWrapper, F, FloatField, IntegerField, OuterRef, Subquery, Sum, Value, When`
- `from django.db.models.functions import Least`
- `from submission.models import KeywordArticle`
- `StaffKeyword` from `wjs.jcom_profile.models`

Merge them into the existing import lines. Then, inside `get_editors_with_keywords`, right after the `editors = editors.annotate(num_shared_kwds=..., max_matching_keyword_weight=...)` block, add:

```python
    editors = editors.annotate(keyword_score=_keyword_score_expression(article))
```

and add this module-level helper just above `get_editors_with_keywords`:

```python
KEYWORD_WEIGHT_CAP = 100


def _keyword_score_expression(article: Article):
    """
    Build the weighted keyword score of an editor (OuterRef("pk")) for ``article``.

    score = sum over shared keywords of article_weight * min(editor_weight, 100),
    divided by sum over all article keywords of article_weight * 100. A NULL article weight
    counts as 100. The result is None when the editor shares no keyword or the article has none.
    Computed in a correlated subquery so the joins of the outer editors query cannot multiply it.
    """
    article_weights = {
        keyword_id: KEYWORD_WEIGHT_CAP if weight is None else weight
        for keyword_id, weight in KeywordArticle.objects.filter(article=article).values_list("keyword_id", "weight")
    }
    if not article_weights:
        return Value(None, output_field=FloatField())
    denominator = float(sum(article_weights.values()) * KEYWORD_WEIGHT_CAP)
    article_weight = Case(
        *[When(keyword_id=keyword_id, then=Value(weight)) for keyword_id, weight in article_weights.items()],
        default=Value(0),
        output_field=IntegerField(),
    )
    weighted_sum = (
        StaffKeyword.objects.filter(
            parameters__user=OuterRef("pk"),
            parameters__journal=article.journal,
            keyword_id__in=list(article_weights),
        )
        .values("parameters__user")
        .annotate(total=Sum(Least(F("weight"), Value(KEYWORD_WEIGHT_CAP)) * article_weight))
        .values("total")
    )
    return ExpressionWrapper(
        Subquery(weighted_sum, output_field=IntegerField()) / Value(denominator),
        output_field=FloatField(),
    )
```

Extend the `get_editors_with_keywords` docstring to mention ``keyword_score`` (the weighted coverage in 0–1, or None; see `_keyword_score_expression`).

Note: if Postgres complains about integer/float division, cast the subquery with `django.db.models.functions.Cast(Subquery(...), FloatField())` instead. Keep the float result either way.

- [ ] **Step 4: Carry the score in `annotate_keyword_ranking`**

In `events/assignment.py`'s `annotate_keyword_ranking`, add a third annotation next to `max_matching_keyword_weight`:

```python
        keyword_score=Subquery(
            annotated_accounts.filter(pk=OuterRef("user_id")).values("keyword_score")[:1],
            output_field=FloatField(),
        ),
```

`FloatField` is already imported there. Add ``keyword_score`` to the function's docstring `:return:` list.

- [ ] **Step 5: Run the tests**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "keyword_score or annotate_keyword_ranking"`.
Expected: all pass.

Then run the whole module: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`. Expected: all pass. Ranking hasn't changed yet.

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/users.py wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py
git commit --no-gpg-sign -m "feat(wjs_review): compute a weighted keyword score for editor candidates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 2: Rank by the score (on !1512)

**Files:**
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (`rank_editor_parameters`)
- Modify: `wjs/plugins/wjs_review/views.py` (`SupervisorAssignEditor._editors_with_keywords`)
- Test: `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py`, `wjs/plugins/wjs_review/tests/test_views.py`

**Interfaces:**
- Consumes: `keyword_score` on `get_editors_with_keywords` and `annotate_keyword_ranking` rows (Task 1).
- Produces: `rank_editor_parameters(qs)` ordered by `keyword_score` desc nulls last, then `available_workload_30d` desc nulls last, then `id`. The manual view uses the same order.

- [ ] **Step 1: Rewrite the obsolete ordering test and add the new ones**

In `test_automatic_article_assignment.py`, **replace** the whole `test_rank_editor_parameters_orders_by_weight_then_count_then_workload` with:

```python
@pytest.mark.django_db
def test_rank_editor_parameters_orders_by_keyword_score_then_workload(article, editors, keywords, journal):
    """
    Final order: highest keyword_score, then highest 30-day availability, then id.

    Replaces the previous "highest single weight, then count" rule (spec 2026-09-29): broad weighted
    coverage (three keywords at 80) now beats a single strong keyword (one at 100).
    """
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 100, keywords[2]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    parameters.update(workload=10, brake_on=0)
    param_broad, param_narrow, param_no_match = parameters.order_by("id")
    for keyword in (keywords[0], keywords[1], keywords[2]):
        StaffKeyword.objects.create(parameters=param_broad, keyword=keyword, weight=80)
    StaffKeyword.objects.create(parameters=param_narrow, keyword=keywords[0], weight=100)

    ranked = list(rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters))))

    assert ranked == [param_broad, param_narrow, param_no_match], "0.8 beats 0.333; no match last"


@pytest.mark.django_db
def test_rank_editor_parameters_breaks_score_ties_by_30d_availability(article, editors, keywords, journal):
    """With equal keyword_score, the editor with more 30-day availability ranks first."""
    _set_article_keyword_weights(article, {keywords[0]: 100})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=journal)
    parameters.update(workload=10, brake_on=0)
    param_busy, param_free, _ = parameters.order_by("id")
    for parameter in (param_busy, param_free):
        StaffKeyword.objects.create(parameters=parameter, keyword=keywords[0], weight=100)
    busy_article = Article.objects.create(journal=journal, title="Busy", owner=param_busy.user)
    WjsEditorAssignment.objects.create(article=busy_article, editor=param_busy.user, editor_type="section-editor")

    ranked = list(rank_editor_parameters(annotate_workload_availability(annotate_keyword_ranking(article, parameters))))

    assert ranked.index(param_free) < ranked.index(param_busy), "tie on score broken by 30-day availability"
```

In `test_rank_editor_parameters_puts_no_keyword_match_last_not_first`:
- change the docstring's "NULL max_matching_keyword_weight" to "NULL keyword_score" and `"-max_matching_keyword_weight"` to `"-keyword_score"`;
- add this assertion after the existing ones: `assert ranked[0].keyword_score is not None, "the matched editor has a score"`.

Add this end-to-end test after `test_default_assign_editors_prefers_keyword_match_over_workload`:

```python
@pytest.mark.django_db
def test_default_assign_editors_prefers_broad_weighted_coverage(
    review_settings,
    admin,
    article,
    editors,
    keywords,
    coauthors_setting,
):
    """Through the submission pipeline, the editor covering more weighted keywords is assigned."""
    article.primary_issue = None
    article.save()
    article.keywords.clear()
    _set_article_keyword_weights(article, {keywords[0]: 100, keywords[1]: 75, keywords[2]: 50})
    parameters = StaffWorkloadParameters.objects.filter(user__in=editors, journal=article.journal)
    parameters.update(workload=10, brake_on=0)
    param_narrow, param_broad, _ = parameters.order_by("id")
    StaffKeyword.objects.create(parameters=param_narrow, keyword=keywords[0], weight=100)
    for keyword in (keywords[0], keywords[1], keywords[2]):
        StaffKeyword.objects.create(parameters=param_broad, keyword=keyword, weight=90)

    with override_settings(WJS_ARTICLE_ASSIGNMENT_FUNCTIONS=WJS_ARTICLE_ASSIGNMENT_FUNCTIONS):
        client = Client()
        client.force_login(admin)
        response = client.post(reverse("submit_review", args=(article.pk,)), data={"next_step": "next_step"})
        assert response.status_code == 302, "submission accepted"

    assert WjsEditorAssignment.objects.get(article=article).editor == param_broad.user, "0.9 beats 0.444"
```

In `test_views.py`, update the docstring of `test_supervisor_assign_editor_ranks_keyword_match_ahead_of_no_match`: say the view orders by `keyword_score` nulls-last. Then append to its assertions:

```python
    assert matched_candidate.keyword_score is not None, "matched editor has a score"
    assert unmatched_candidate.keyword_score is None, "unmatched editor has no score"
```

Add a new manual-view test after it:

```python
@pytest.mark.django_db
def test_supervisor_assign_editor_ranks_by_keyword_score(
    submitted_workflow: ArticleWorkflow,
    fake_request: HttpRequest,
    section_editor: JCOMProfile,
    create_jcom_user: Callable,
    keywords,
    review_settings,
):
    """The manual list orders by keyword_score: broad weighted coverage ahead of one strong keyword."""
    journal = submitted_workflow.article.journal
    for keyword in (keywords[0], keywords[1], keywords[2]):
        submitted_workflow.article.keywords.add(keyword)

    def _editor(name, weights):
        editor = create_jcom_user(name)
        editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
        parameters = StaffWorkloadParameters.objects.create(
            user=editor.janeway_account, journal=journal, workload=10, enabled=True
        )
        for keyword, weight in weights:
            StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=weight)
        return editor.janeway_account

    narrow = _editor("narrow_section_editor", [(keywords[0], 100)])
    broad = _editor("broad_section_editor", [(keywords[0], 80), (keywords[1], 80), (keywords[2], 80)])

    fake_request.user = section_editor.janeway_account
    view = SupervisorAssignEditor()
    view.request = fake_request
    view.args = (submitted_workflow.article.pk,)
    view.kwargs = {}
    view.setup(fake_request)
    view.object = submitted_workflow

    candidates = list(view._editors_with_keywords(search_text=None))

    assert candidates.index(broad) < candidates.index(narrow), "keyword_score orders the manual list"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py -k "rank_editor_parameters or broad_weighted" wjs/plugins/wjs_review/tests/test_views.py -k "supervisor_assign_editor"`.
Expected: `test_rank_editor_parameters_orders_by_keyword_score_then_workload`, `test_default_assign_editors_prefers_broad_weighted_coverage` and `test_supervisor_assign_editor_ranks_by_keyword_score` FAIL. They get the narrow editor first.

- [ ] **Step 3: Change the ranking**

In `rank_editor_parameters`, replace the `order_by(...)` with:

```python
    return users_parameters.order_by(
        F("keyword_score").desc(nulls_last=True),
        F("available_workload_30d").desc(nulls_last=True),
        "id",
    )
```

Rewrite its docstring: "Apply the canonical final ordering: weighted keyword score, then 30-day availability, then id." Keep the paragraph explaining `nulls_last=True`, replacing `max_matching_keyword_weight` with `keyword_score`. Also update its `:param users_parameters:` line to mention `keyword_score`.

In `views.py` `SupervisorAssignEditor._editors_with_keywords`, replace:

```python
            .order_by(
                F("max_matching_keyword_weight").desc(nulls_last=True),
                "-num_shared_kwds",
                F("available_workload_30d").desc(nulls_last=True),
                "id",
            )
```

with:

```python
            .order_by(
                F("keyword_score").desc(nulls_last=True),
                F("available_workload_30d").desc(nulls_last=True),
                "id",
            )
```

- [ ] **Step 4: Run the modules**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_views.py`.
Expected: all pass.

If another existing test fails because it encoded "weight first" (for example an assertion that a single high-weight editor wins over broader coverage), update its expectation to the new rule. Add a one-line comment `# Ranking is by weighted keyword score since spec 2026-09-29.` and list every such test in the report. Don't change a test that fails for any other reason: report it instead.

- [ ] **Step 5: Commit**

```bash
git add wjs/plugins/wjs_review/events/assignment.py wjs/plugins/wjs_review/views.py wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_views.py
git commit --no-gpg-sign -m "feat(wjs_review): rank editor candidates by weighted keyword score

Automatic assignment and the manual Select new editor list now order by
keyword_score, then 30-day availability, then id, instead of the highest
single keyword weight and the shared-keyword count.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 3: Show the score on the "Select new editor" page (on !1512)

**Files:**
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_table.html`
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/editor_list.html`
- Test: `wjs/plugins/wjs_review/tests/test_views.py`

**Interfaces:**
- Consumes: `user.keyword_score` on each listed editor (Task 1).

- [ ] **Step 1: Write the failing test**

Append to `test_views.py`. Add `from django.test import override_settings` if it isn't already imported.

```python
@pytest.mark.django_db
def test_select_new_editor_page_shows_keyword_score(
    submitted_workflow: ArticleWorkflow,
    eo_user: JCOMProfile,
    create_jcom_user: Callable,
    keywords,
    review_settings,
):
    """The manual editor table shows each editor's keyword score as a percentage, or a dash."""
    journal = submitted_workflow.article.journal
    submitted_workflow.article.keywords.add(keywords[0], keywords[1])
    for name, weights in (("scored_editor", [(keywords[0], 100)]), ("unscored_editor", [])):
        editor = create_jcom_user(name)
        editor.add_account_role(constants.SECTION_EDITOR_ROLE, journal)
        parameters = StaffWorkloadParameters.objects.create(
            user=editor.janeway_account, journal=journal, workload=10, enabled=True
        )
        for keyword, weight in weights:
            StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=weight)
    client = Client()
    client.force_login(eo_user.janeway_account)

    with override_settings(WJS_SHOW_EDITOR_KEYWORDS=[journal.code]):
        response = client.get(reverse("wjs_assign_editor", args=(submitted_workflow.pk,)))

    content = response.content.decode()
    assert response.status_code == 200, "EO can open the page"
    assert "Keyword score" in content, "column header shown"
    assert "50%" in content, "one of two equally weighted keywords at full weight = 50%"
    assert "—" in content, "editors without a shared keyword show a dash"
```

If `eo_user` can't open this page (403), use the same user and setup as the existing `test_eo_select_new_editor` (in the same file) instead, and note the change in the report.

- [ ] **Step 2: Run it to verify it fails**

Run the tests with `wjs/plugins/wjs_review/tests/test_views.py -k shows_keyword_score`.
Expected: FAIL on `"Keyword score" in content`.

- [ ] **Step 3: Add the column**

In `editor_table.html`, inside the `{% if ... show_editor_keywords %}` header block, right after the `N. Matching Keywords` `<th>`, add:

```django
        <th scope="col" class="col-1">{% translate "Keyword score" %}</th>
```

In `editor_list.html`, inside the matching `{% if workflow.article.journal|show_editor_keywords %}` block, right after `<td>{{ user.num_shared_kwds }}</td>`, add:

```django
      <td>{% if user.keyword_score is not None %}{% widthratio user.keyword_score 1 100 %}%{% else %}—{% endif %}</td>
```

Check the column widths in `editor_table.html` still add up sensibly. If the header row's `col-*` classes then exceed 12, reduce the Keywords column from `col-4` to `col-3`.

- [ ] **Step 4: Run the tests and djlint**

Run the tests with `wjs/plugins/wjs_review/tests/test_views.py`. Expected: all pass.

Then run `cd /home/yakky/Projects/sissa/wjs-profile-project && djlint --profile=django wjs/plugins/wjs_review/templates/wjs_review/assign_editor/`. Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add wjs/plugins/wjs_review/templates/wjs_review/assign_editor/elements/ wjs/plugins/wjs_review/tests/test_views.py
git commit --no-gpg-sign -m "feat(wjs_review): show the keyword score on the Select new editor page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 4: Docs and regression on !1512

**Files:**
- Modify: `wjs/plugins/wjs_review/CLAUDE.md`
- Create: `docs/superpowers/specs/2026-09-29-weighted-keyword-score-wiki-text.md`

- [ ] **Step 1: Update `wjs_review/CLAUDE.md`**

Find every place that describes the keyword ranking: grep for `max_matching_keyword_weight`, `rank_editor_parameters` and `weight, then`. Update each one to say that candidates are ranked by `keyword_score` (weighted keyword coverage, see `users.py::_keyword_score_expression`), then `available_workload_30d`, then `id`, for both automatic assignment and the manual Select new editor page. If the file has no such description, add one short paragraph in its assignment section.

- [ ] **Step 2: Write the wiki replacement text**

Create `docs/superpowers/specs/2026-09-29-weighted-keyword-score-wiki-text.md` with this content:

```markdown
# Editor Selection logic — replacement text for the keyword-ranking rule

Paste this into the "Keywords matching" part of https://gitlab.sissamedialab.it/wjs/specs/-/wikis/Editor-Selection-logic, replacing the "highest keyword weight, then number of matching keywords" rule.

## Keyword score

Each candidate editor gets a keyword score between 0 and 1:

    score = Σ over keywords shared with the article of (article weight × editor weight)
            ──────────────────────────────────────────────────────────────────────────
            Σ over all article keywords of (article weight × 100)

- The article weight is the author's keyword weight (100 / 75 / 50 / 25 by position). A missing weight counts as 100.
- The editor weight is the editor's configured keyword weight. Values above 100 count as 100.
- An editor sharing no keyword with the article has no score and is ranked after every editor with a score.

## Ranking

Candidates are ordered by:
1. keyword score, highest first;
2. availability over the last 30 days, highest first;
3. internal id.

Editors at their monthly limit are still excluded from automatic assignment. The same order is used on the "Select new editor" page, which also shows each editor's score.
```

- [ ] **Step 3: Regression and lint**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_views.py wjs/jcom_profile/tests/test_staff_parameters.py`. Expected: all pass.

Then run `cd /home/yakky/Projects/sissa/wjs-profile-project && pre-commit run --files $(git diff --name-only a99e180c..HEAD)`. Expected: pass.

- [ ] **Step 4: Commit (don't push)**

```bash
git add wjs/plugins/wjs_review/CLAUDE.md docs/superpowers/specs/2026-09-29-weighted-keyword-score-wiki-text.md
git commit --no-gpg-sign -m "docs: document the weighted keyword ranking and the wiki replacement text

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 5: Bring !1512 into !1513 and !1534

**Files:** git only. The merge commits are local; don't push.

**Interfaces:**
- Produces: `feature/issue-3136-automatic-assignment-audit` containing Tasks 1–4. `matched_parameter.keyword_score` is then available in `default_assign_editors_to_articles`.

- [ ] **Step 1: Merge into the design branch**

```bash
cd /home/yakky/Projects/sissa/wjs-profile-project
git switch docs/automatic-assignment-audit-design
git merge --no-gpg-sign --no-edit feature/automatic-assignment-keyword-matching
```

Expected: a clean merge. If it conflicts, run `git merge --abort` and report.

- [ ] **Step 2: Merge into the audit branch**

```bash
git switch feature/issue-3136-automatic-assignment-audit
git merge --no-gpg-sign --no-edit docs/automatic-assignment-audit-design
git status --short
```

If it conflicts, it will most likely be in `events/assignment.py` (`rank_editor_parameters` or `annotate_keyword_ranking`) or `tests/test_automatic_article_assignment.py`. Resolve so that:
- the ranking and annotation code equal !1512's version (Tasks 1–2);
- the audit capture code in `default_assign_editors_to_articles` and `jcom_assign_editors_to_articles` equals !1534's version;
- both sides' tests are kept.

Then commit the merge with the message `merge: bring the weighted keyword score into the audit branch`, plus the trailers.

- [ ] **Step 3: Run the affected modules**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_views.py wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py --create-db`.
Expected: all pass.

If an existing !1534 audit assertion now fails because a different editor wins under the new ranking (for example `test_default_assign_editors_picks_best_workload_when_no_keyword_match` or the no-keywords test), check its setup. When the test's intent still holds, update its expectation to the new rule and add the comment `# Ranking is by weighted keyword score since spec 2026-09-29.` Report each such change. If you had to change anything, commit it as `test(wjs_review): align audit tests with the weighted keyword ranking`.

---

### Task 6: Record and report the score in the audit (on !1534)

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (`AutomaticAssignmentAudit`)
- Create: `wjs/plugins/wjs_review/migrations/0020_automaticassignmentaudit_keyword_score.py` (generated)
- Modify: `wjs/plugins/wjs_review/events/assignment.py` (`record_automatic_assignment_audit`, default-path `audit_data`)
- Modify: `wjs/plugins/wjs_review/templates/wjs_review/automatic_assignment_audit/audit_list.html`
- Modify: `wjs/plugins/wjs_review/management/commands/replay_automatic_assignment.py`
- Modify: `docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md` (rev 1.3), `wjs/plugins/wjs_review/CLAUDE.md`
- Test: `tests/test_automatic_article_assignment.py`, `tests/test_automatic_assignment_audit.py`, `tests/test_replay_automatic_assignment.py`

**Interfaces:**
- Consumes: `matched_parameter.keyword_score` (Tasks 1 and 5).
- Produces:
    - `AutomaticAssignmentAudit.keyword_score: float | None`;
    - `record_automatic_assignment_audit(..., keyword_score: float | None = None)`;
    - the CSV column `keyword_score`, after `max_matching_keyword_weight`.

- [ ] **Step 1: Write the failing tests**

In `test_automatic_article_assignment.py`:
- In `test_default_assign_editors_prefers_keyword_match_over_workload`, append the assertion below. The article's single keyword has a NULL weight (counts as 100), and the editor's weight is 1, so the score is `(100×1)/(100×100) = 0.01`:

  ```python
      assert audit.keyword_score == pytest.approx(0.01), "single keyword, editor weight 1 of 100"
  ```
- In `test_default_special_issue_assignment_ignores_keywords`, append: `assert audit.keyword_score is None, "special issues don't use keywords"`.
- In `test_jcom_assign_editors_ignores_keywords`, append: `assert audit.keyword_score is None, "JCOM doesn't use keywords"`.

In `test_automatic_assignment_audit.py`, append:

```python
@pytest.mark.django_db
def test_audit_page_shows_keyword_score(article, editors, eo_user):
    """The audit page shows the keyword score as a percentage, and a dash for legacy rows."""
    _make_audit(article, editors[0], keyword_score=0.52)
    _make_audit(article, editors[1], keyword_score=None)
    client = Client()
    client.force_login(eo_user)

    content = client.get(reverse("wjs-automatic-assignment-audit")).content.decode()

    assert "Keyword score" in content, "column header shown"
    assert "52%" in content, "score rendered as a whole percentage"
```

In `test_replay_automatic_assignment.py`, in `test_command_writes_csv_report`, insert `"keyword_score",` into the expected column list right after `"max_matching_keyword_weight",`.

- [ ] **Step 2: Run them to verify they fail**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py -k "prefers_keyword_match or special_issue_assignment_ignores or jcom_assign_editors_ignores or shows_keyword_score or csv_report"`.
Expected: FAIL (`keyword_score` unknown).

- [ ] **Step 3: Add the field and its migration**

In `models.py`, add this right after `workload_available_30d` in `AutomaticAssignmentAudit`:

```python
    keyword_score = models.FloatField(_("Keyword score"), null=True, blank=True)
```

Extend the model docstring: "``keyword_score`` is the chosen editor's weighted keyword coverage (0–1) on the default path; empty elsewhere."

Then run:

```bash
cd /home/yakky/Projects/sissa/janeway/src && python manage.py makemigrations wjs_review -n automaticassignmentaudit_keyword_score
```

Expected: `0020_automaticassignmentaudit_keyword_score.py` with a single `AddField`, depending on `0019_automaticassignmentaudit_workload_available_30d`. Delete any spurious `submission/migrations/*.py` generated in the Janeway checkout. Then run `python manage.py makemigrations wjs_review --check --dry-run` → `No changes detected`.

- [ ] **Step 4: Record it**

In `record_automatic_assignment_audit`, add the keyword parameter `keyword_score: float | None = None` after `workload_available_30d`, and pass it to `create(...)`. In `default_assign_editors_to_articles`' non-special-issue `audit_data`, add `"keyword_score": matched_parameter.keyword_score,`.

- [ ] **Step 5: Show it on the page and in the CSV**

In `audit_list.html`, add a header cell after `{% translate "Max keyword weight" %}`:

```django
            <th scope="col">{% translate "Keyword score" %}</th>
```

Add the matching body cell after the `max_matching_keyword_weight` cell:

```django
              <td>{% if audit.keyword_score is not None %}{% widthratio audit.keyword_score 1 100 %}%{% else %}—{% endif %}</td>
```

Increase the empty-row `colspan` by one.

In `replay_automatic_assignment.py`, insert `"keyword_score",` into `REPORT_FIELDS` after `"max_matching_keyword_weight",`. Add this to `report_row_to_dict` after the `max_matching_keyword_weight` entry:

```python
        "keyword_score": (
            f"{audit.keyword_score:.3f}" if audit and audit.keyword_score is not None else ""
        ),
```

The test in `test_audit_page_renders_rows_without_30d_value` targets the last `<td>` of the row. It must keep pointing at the 30-day cell, so don't reorder columns after `workload_available_30d`.

- [ ] **Step 6: Docs**

- In the audit spec, add the revision row `| 1.3 | Iacopo Spalletti (with Claude Opus 5.5) | 2026-09-29 | Adds keyword_score (weighted keyword coverage, spec 2026-09-29-weighted-keyword-score-design) to the audit record, audit page and replay CSV |`.
- In the spec's data-model table, add the row: `keyword_score` | nullable `FloatField` | chosen editor's weighted keyword score (0–1), default path only.
- In its CSV field list, add `keyword_score` after `max_matching_keyword_weight`.
- In `wjs_review/CLAUDE.md`'s audit paragraph, add that the audit records `keyword_score` on the default path.

- [ ] **Step 7: Run the tests and commit**

Run the tests with `wjs/plugins/wjs_review/tests/test_automatic_article_assignment.py wjs/plugins/wjs_review/tests/test_automatic_assignment_audit.py wjs/plugins/wjs_review/tests/test_replay_automatic_assignment.py --create-db`. Expected: all pass.

Run djlint on `automatic_assignment_audit/` and pre-commit on the changed files.

```bash
git add wjs/plugins/wjs_review/ docs/superpowers/specs/2026-09-18-automatic-assignment-audit-design.md
git commit --no-gpg-sign -m "feat(wjs_review): record and report the keyword score in the assignment audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CA7dseH8k64y7fnkz6Empu"
```

---

### Task 7: Acceptance replay (controller-run; needs the user)

The local DB already holds the 40 replayed articles, so the replay would skip them. **Ask the user to re-import the snapshot first.** Then, on `feature/issue-3136-automatic-assignment-audit`:

1. Run `python manage.py migrate wjs_review 0020`.
2. Reapply the keyword duplicate remap, the same script as on 2026-09-29. Expected: 889 moved, 5 merged.
3. Restore the **same** per-editor limits from `docs/superpowers/reports/2026-09-29-jcap-limits-3-5-seed3136.csv` (set `workload = new_workload` by `parameters_id`), rather than re-randomising.
4. Replay the same 40 article ids with `--output-file …/replay-jcap-40-weighted-score.csv`.
5. Report:
    - the match count against 6/40;
    - the historical editor's rank (1st / top 3) against the offline estimate (13/40, 29/40);
    - the deciding-factor breakdown;
    - the `keyword_score` distribution of winners.
