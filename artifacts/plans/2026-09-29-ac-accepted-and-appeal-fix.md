# AC fixes on paper acceptance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop "Appeal to submit" from leaking past `UnderAppeal`, clean up the rows that already leaked, and add the EO-only "Access mode to check" AC for papers held in `Accepted`.

**Architecture:** Materialized attention conditions (`AttentionCondition` rows) are written only by explicit `ac_service` calls inside logic classes (`# -- Materialized AC updates --` blocks) and by the `ACStateEvaluator` (state-scoped, driven by `STATE_ROLE_AC_MAP`). We add explicit resolves where the appeal is submitted, a new event-based code created where the acceptance checks hold a paper in `Accepted`, a new logic class for the EO confirmation that resolves it, and a data migration for the stale rows. No Django signals.

**Tech Stack:** Django 5.2, django-fsm, pytest / pytest-django, Janeway plugin `wjs_review` (in `wjs-profile-project`).

**Spec:** `artifacts/specs/2026-09-29-ac-accepted-and-appeal-fix-design.md`

## Global Constraints

- Documentation language: **English** (code comments, docstrings, commit messages).
- No Django signals: every AC change is an explicit `ac_service` call in a logic class, in a `# -- Materialized AC updates --` block.
- New code value: `ACCESS_MODE_TO_CHECK = "access_mode_to_check"`; message text exactly `"Access mode to check"`; role `"eo"` only; state `"Accepted"`.
- Appeal ACs resolved: `APPEAL_TO_SUBMIT` for role `"author"`, `APPEAL_LATE` for role `"eo"`.
- Data migration: `wjs/plugins/wjs_review/migrations/0018_resolve_stale_appeal_acs.py`, depends on `("wjs_review", "0017_blacklisted_authoremail")`, literal codes/states only, reverse is `RunPython.noop`.
- `AuthorHandleRevisionObsolete` is **not** touched.
- The EO confirmation view keeps its current user-facing messages: success `"Article confirmed as ready for typesetter."`, error `"This article cannot transition to Ready for Typesetter in its current state."`.

## How to run tests

Tests only run inside the Janeway environment, **from `janeway/src`**, pointing at this repo by relative path (see `.claude/rules/tests.md`). The `pytest_freezegun` plugin is broken on Python 3.13, so disable it:

```bash
cd /home/andrea/Devel/sissa/janeway/src
python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/<file>.py::<test> -v
```

Migrations are skipped in tests (`SkipMigrations`), so the data migration is tested by importing its module and calling the forward function directly.

**Environment prerequisite:** the `janeway` venv must have this repo's current requirements installed (the latest `wjs-develop` added `drf-spectacular`). If existing tests such as `test_attentionconditions.py::test_author_appeal_is_late` fail at fixture setup, fix the environment before starting Task 1.

## Review Focus

1. **The corresponding author is also an EO user** (the case in the #3174 report): after the appeal is submitted, `article_requires_attention` for that user must not return "Appeal to submit". Pinned in Task 1.
2. **Double submission of "Confirm production readiness"** (second POST when the paper is already in `ReadyForTypesetter`): the view must show the error message and redirect, not raise. Pinned in Task 4.
3. **Non-EO user posting to the confirmation URL** (e.g. the author): transition refused, state unchanged, AC still active. Pinned in Task 4.
4. **Paper held in `Accepted` and then withdrawn:** "Access mode to check" must not survive. Pinned in Task 4 (relies on the existing `WithdrawPreprint` resolve-all).
5. **Paper still `UnderAppeal` when the data migration runs:** its active appeal ACs must stay active. Pinned in Task 2.

---

### Task 1: Resolve the appeal ACs when the author submits the appeal

**Files:**
- Modify: `wjs/plugins/wjs_review/logic.py` — `AuthorHandleRevision.run`, the `# -- Materialized AC updates --` block (around lines 2303-2339)
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Consumes: `ac_service.resolve_for_role(article, role, code)`, `ac_service.APPEAL_TO_SUBMIT`, `ac_service.APPEAL_LATE`, `AuthorHandleRevision._was_under_appeal()`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Add to the imports of `test_attentionconditions.py`:

```python
from plugins.wjs_review.logic import AuthorHandleRevision
from plugins.wjs_review.models import RevisionStorage
from plugins.wjs_submission.revision import RevisionStartConfirmView
```

(merge `AuthorHandleRevision` into the existing `from plugins.wjs_review.logic import (...)` block and `RevisionStorage` into the existing `from plugins.wjs_review.models import (...)` block, keeping alphabetical order).

Append the test:

```python
@pytest.mark.django_db
def test_appeal_acs_resolved_when_author_submits_appeal(
    under_appeal_article: Article,
    fake_request: HttpRequest,
):
    """Submitting the appeal resolves the UnderAppeal ACs (specs#3174).

    APPEAL_TO_SUBMIT is event-based: nothing else (stale cleanup, nightly
    rebuild) would ever resolve it once the paper left UnderAppeal.
    """
    article = under_appeal_article
    author = article.correspondence_author
    eo = get_eo_user(article)

    # Make APPEAL_LATE active too: the appeal is overdue.
    openappeal_err = EditorRevisionRequest.objects.get(
        article_id=article.id,
        date_completed__isnull=True,
        type=ArticleWorkflow.Decisions.OPEN_APPEAL,
    )
    openappeal_err.date_due = now() - timezone.timedelta(days=5)
    openappeal_err.save()
    attention_conditions_rebuild(article)
    assert AttentionCondition.objects.active().filter(article=article, code=ac_service.APPEAL_TO_SUBMIT).exists()
    assert AttentionCondition.objects.active().filter(article=article, code=ac_service.APPEAL_LATE).exists()

    # The author submits the appeal through the wjs_submission revision flow.
    fake_request.user = author
    RevisionStartConfirmView()._init_revision_flow(article_id=article.pk)
    revision_storage = RevisionStorage.objects.get(article=article)
    revision_storage.data.update({"comments_editor": "author_note", "submission_requirements": True})
    revision_storage.save()
    AuthorHandleRevision(request=fake_request, article=article).run()

    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
    assert not AttentionCondition.objects.active().filter(article=article, code=ac_service.APPEAL_TO_SUBMIT).exists()
    assert not AttentionCondition.objects.active().filter(article=article, code=ac_service.APPEAL_LATE).exists()
    state_cls = getattr(states, article.articleworkflow.state)
    assert state_cls.article_requires_attention(article=article, user=author) != "Appeal to submit"
    assert "Appeal is" not in state_cls.article_requires_attention(article=article, user=eo)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py::test_appeal_acs_resolved_when_author_submits_appeal -v`
Expected: FAIL on `assert not AttentionCondition.objects.active().filter(... APPEAL_TO_SUBMIT ...)`.

- [ ] **Step 3: Implement the resolves**

In `AuthorHandleRevision.run` (`logic.py`), right after the three existing `ac_service.resolve_for_role_batch(...)` calls and before `ac_service.evaluate_blacklisted_author(article)`, add:

```python
            if self._was_under_appeal():
                # Appeal submitted: the paper left UnderAppeal, its ACs no longer apply
                ac_service.resolve_for_role(article, "author", ac_service.APPEAL_TO_SUBMIT)
                ac_service.resolve_for_role(article, "eo", ac_service.APPEAL_LATE)
```

- [ ] **Step 4: Run the test to verify it passes**

Run the same command as Step 2. Expected: PASS.
Also run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py -q` — Expected: all pass.

- [ ] **Step 5: Commit (only if per-change commits were chosen at flow step 4)**

```bash
git add wjs/plugins/wjs_review/logic.py wjs/plugins/wjs_review/tests/test_attentionconditions.py
git commit -m "fix(wjs_review): resolve appeal ACs when the author submits the appeal"
```

---

### Task 2: Data migration resolving stale appeal ACs

**Files:**
- Create: `wjs/plugins/wjs_review/migrations/0018_resolve_stale_appeal_acs.py`
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions.py`

**Interfaces:**
- Consumes: models `wjs_review.AttentionCondition` (`code`, `status`, `article`), `wjs_review.ArticleWorkflow` (`state`, one-to-one `article`).
- Produces: module-level function `resolve_stale_appeal_acs(apps, schema_editor) -> None` in the migration module.

- [ ] **Step 1: Write the failing test**

Add `import importlib` and `from django.apps import apps as django_apps` to the imports of `test_attentionconditions.py`, then append:

```python
@pytest.mark.django_db
def test_migration_resolves_stale_appeal_acs(
    under_appeal_article: Article,
    accepted_article: Article,
):
    """The data migration resolves appeal ACs only on papers no longer UnderAppeal."""
    migration = importlib.import_module("plugins.wjs_review.migrations.0018_resolve_stale_appeal_acs")
    author = accepted_article.correspondence_author
    # Simulate the leaked row (what the #3174 bug left in the database).
    ac_service.upsert_ac(accepted_article, author, ac_service.APPEAL_TO_SUBMIT, "Appeal to submit")
    ac_service.upsert_ac(accepted_article, get_eo_user(accepted_article), ac_service.APPEAL_LATE, "late")
    # Legitimate row: the paper is still under appeal.
    attention_conditions_rebuild(under_appeal_article)
    assert AttentionCondition.objects.active().filter(
        article=under_appeal_article, code=ac_service.APPEAL_TO_SUBMIT
    ).exists()

    migration.resolve_stale_appeal_acs(django_apps, None)

    assert not AttentionCondition.objects.active().filter(
        article=accepted_article, code__in=[ac_service.APPEAL_TO_SUBMIT, ac_service.APPEAL_LATE]
    ).exists()
    assert AttentionCondition.objects.active().filter(
        article=under_appeal_article, code=ac_service.APPEAL_TO_SUBMIT
    ).exists()
```

Note: `under_appeal_article` and `accepted_article` both depend on `assigned_article`; if pytest resolves them to the **same** article (shared fixture instance), replace `accepted_article` with a second article built via the existing `_accept_article(fake_request, <another assigned article>)` helper from `conftest.py`. Verify by asserting `under_appeal_article.pk != accepted_article.pk` as the first line of the test.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py::test_migration_resolves_stale_appeal_acs -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plugins.wjs_review.migrations.0018_resolve_stale_appeal_acs'`.

- [ ] **Step 3: Create the migration**

```python
"""Resolve appeal ACs left active on papers no longer under appeal (specs#3174).

Until specs#3174, submitting an appeal did not resolve APPEAL_TO_SUBMIT
(author) and APPEAL_LATE (EO). APPEAL_TO_SUBMIT is event-based, so neither the
stale-AC cleanup nor the nightly rebuild healed those rows.
"""

from django.db import migrations

APPEAL_CODES = ("appeal_to_submit", "appeal_late")
UNDER_APPEAL = "UnderAppeal"


def resolve_stale_appeal_acs(apps, schema_editor):
    """Mark as resolved the active appeal ACs of papers not in UnderAppeal."""
    AttentionCondition = apps.get_model("wjs_review", "AttentionCondition")
    AttentionCondition.objects.filter(code__in=APPEAL_CODES, status="active").exclude(
        article__articleworkflow__state=UNDER_APPEAL,
    ).update(status="resolved")


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0017_blacklisted_authoremail"),
    ]

    operations = [
        migrations.RunPython(resolve_stale_appeal_acs, migrations.RunPython.noop),
    ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run the same command as Step 2. Expected: PASS.
Also run from `janeway/src`: `python manage.py makemigrations wjs_review --check --dry-run` — Expected: `No changes detected` (the migration adds no schema changes).

- [ ] **Step 5: Commit (only if per-change commits were chosen)**

```bash
git add wjs/plugins/wjs_review/migrations/0018_resolve_stale_appeal_acs.py wjs/plugins/wjs_review/tests/test_attentionconditions.py
git commit -m "fix(wjs_review): resolve stale appeal ACs with a data migration"
```

---

### Task 3: "Access mode to check" AC created when a paper is held in Accepted

**Files:**
- Modify: `wjs/plugins/wjs_review/ac_service.py` — event-based constants (after `APPEAL_TO_SUBMIT`, ~line 108), `STATE_ROLE_AC_MAP` (~line 750), evaluators (after `_evaluate_appeal_to_submit`, ~line 1117)
- Modify: `wjs/plugins/wjs_review/logic__production.py` — `VerifyProductionRequirements.run` (~lines 145-154)
- Create test: `wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py`

**Interfaces:**
- Consumes: `ACStateEvaluator(state, article)._evaluate_code(code)`, `_upsert_for_role(role, code, message)`.
- Produces: `ac_service.ACCESS_MODE_TO_CHECK: str == "access_mode_to_check"`; map entry `STATE_ROLE_AC_MAP[("Accepted", "eo")] == [ACCESS_MODE_TO_CHECK]`; method `ACStateEvaluator._evaluate_access_mode_to_check(self, roles: list[str]) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `test_attentionconditions_accepted.py`:

```python
"""Attention conditions for papers held in the Accepted state (specs#3174)."""

import pytest
from django.http import HttpRequest
from plugins.wjs_review import ac_service, states
from plugins.wjs_review.ac_service import ACStateEvaluator
from plugins.wjs_review.models import ArticleWorkflow, AttentionCondition, WjsEditorAssignment
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _accept_article

HOLD_IN_ACCEPTED = "wjs_review.events.checks.always_reject"
PASS_TO_TYPESETTER = "wjs_review.events.checks_after_acceptance.always_pass"


def _accept_with_checks(article: Article, fake_request: HttpRequest, settings, check_function: str) -> Article:
    """Accept the article with the given acceptance check function configured for its journal."""
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: [check_function]}
    fake_request.user = WjsEditorAssignment.objects.get_current(article).editor
    _accept_article(fake_request, article)
    article.refresh_from_db()
    return article


def _requires_attention(article: Article, user) -> str:
    state_cls = getattr(states, article.articleworkflow.state)
    return state_cls.article_requires_attention(article=article, user=user)


@pytest.mark.django_db
def test_access_mode_to_check_set_for_eo_when_held_in_accepted(assigned_article, fake_request, settings):
    """A paper held in Accepted by the acceptance checks gets the AC, for the EO only."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED

    eo = get_eo_user(article)
    editor = WjsEditorAssignment.objects.get_current(article).editor
    author = article.correspondence_author
    assert AttentionCondition.objects.active().filter(
        article=article, user=eo, code=ac_service.ACCESS_MODE_TO_CHECK
    ).exists()
    assert _requires_attention(article, eo) == "Access mode to check"
    assert _requires_attention(article, editor) != "Access mode to check"
    assert _requires_attention(article, author) != "Access mode to check"


@pytest.mark.django_db
def test_access_mode_to_check_not_set_when_checks_pass(assigned_article, fake_request, settings):
    """A paper that passes the acceptance checks goes to ReadyForTypesetter without the AC."""
    article = _accept_with_checks(assigned_article, fake_request, settings, PASS_TO_TYPESETTER)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert not AttentionCondition.objects.filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()


@pytest.mark.django_db
def test_evaluator_creates_access_mode_to_check_for_accepted(assigned_article, fake_request, settings):
    """The evaluator (nightly rebuild) creates the AC for papers already in Accepted."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    AttentionCondition.objects.filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).delete()

    ACStateEvaluator(state=ArticleWorkflow.ReviewStates.ACCEPTED, article=article).evaluate_all()

    assert AttentionCondition.objects.active().filter(
        article=article, user=get_eo_user(article), code=ac_service.ACCESS_MODE_TO_CHECK
    ).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py -v`
Expected: FAIL with `AttributeError: module 'plugins.wjs_review.ac_service' has no attribute 'ACCESS_MODE_TO_CHECK'`.

- [ ] **Step 3: Add the code, map entry and evaluator in `ac_service.py`**

After the `APPEAL_TO_SUBMIT` constant and its docstring:

```python
ACCESS_MODE_TO_CHECK = "access_mode_to_check"
"""EO: the accepted paper is held in Accepted; access mode to be checked before production."""
```

In `STATE_ROLE_AC_MAP`, after the `# -- UnderAppeal --` entries:

```python
    # -- Accepted --
    ("Accepted", "eo"): [ACCESS_MODE_TO_CHECK],
```

After `_evaluate_appeal_to_submit`:

```python
    def _evaluate_access_mode_to_check(self, roles: list[str]) -> None:
        """EO: paper held in Accepted, access mode to check.

        Always active in this state: a paper stays in Accepted only when the
        acceptance checks block it (e.g. JCAP TA papers), until the EO confirms
        production readiness.
        """
        for role in roles:
            self._upsert_for_role(role, ACCESS_MODE_TO_CHECK, "Access mode to check")
```

- [ ] **Step 4: Create the AC in `VerifyProductionRequirements.run`**

Replace the failure branch in `logic__production.py`:

```python
            if not self._check_conditions():
                # Here we do not raise an exception, because doing so would prevent an editor from accepting an
                # article. Instead we send a message to EO.
                self._log_acceptance_issues()

                # -- Materialized AC updates --
                from . import ac_service

                # Paper held in Accepted: the EO must check it before production.
                # ACCESS_MODE_TO_CHECK is event-based: this is its creation point;
                # ConfirmProductionReadiness resolves it on the way out.
                evaluator = ac_service.ACStateEvaluator(
                    state=self.articleworkflow.state, article=self.articleworkflow.article
                )
                evaluator._evaluate_code(ac_service.ACCESS_MODE_TO_CHECK)
```

(`ConfirmProductionReadiness` is created in Task 4; the comment already names it.)

- [ ] **Step 5: Run the tests to verify they pass**

Run the same command as Step 2. Expected: 3 passed.
Also run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_workflow.py::test_accepted_workflow_issues ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py -q` — Expected: all pass.

- [ ] **Step 6: Commit (only if per-change commits were chosen)**

```bash
git add wjs/plugins/wjs_review/ac_service.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py
git commit -m "feat(wjs_review): add Access mode to check AC for papers held in Accepted"
```

---

### Task 4: `ConfirmProductionReadiness` logic class resolves the AC

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py` — add `from django_fsm import has_transition_perm` (next to the existing `from django_fsm import can_proceed`, as `from django_fsm import can_proceed, has_transition_perm`) and the new class right after `VerifyProductionRequirements`
- Modify: `wjs/plugins/wjs_review/views__production.py` — `EOConfirmsProductionReady.post` (~lines 662-668) and the `from .logic__production import (...)` block
- Test: `wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py`

**Interfaces:**
- Consumes: `ac_service.ACCESS_MODE_TO_CHECK` (Task 3), `ac_service.resolve_for_role`, `ArticleWorkflow.system_verifies_production_requirements` (FSM transition, permission `has_eo_role_by_article`).
- Produces: `ConfirmProductionReadiness(workflow: ArticleWorkflow, user: Account).run() -> ArticleWorkflow`, raises `ValueError` when the transition is not allowed for `user`.

- [ ] **Step 1: Write the failing tests**

Append to `test_attentionconditions_accepted.py` (add `from plugins.wjs_review.logic__production import ConfirmProductionReadiness`, `from plugins.wjs_review.logic import WithdrawPreprint` and `from django.test import Client` to the imports):

```python
def _confirm_url(article: Article) -> str:
    return f"/{article.journal.code}/plugins/wjs-review-articles/confirm_production_ready/{article.articleworkflow.pk}/"


@pytest.mark.django_db
def test_confirm_production_readiness_resolves_ac(assigned_article, fake_request, settings):
    """The EO confirmation moves the paper to ReadyForTypesetter and resolves the AC."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    eo = get_eo_user(article)

    workflow = ConfirmProductionReadiness(workflow=article.articleworkflow, user=eo).run()

    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert not AttentionCondition.objects.active().filter(
        article=article, code=ac_service.ACCESS_MODE_TO_CHECK
    ).exists()


@pytest.mark.django_db
def test_confirm_production_readiness_refused_for_non_eo(assigned_article, fake_request, settings):
    """A non-EO user cannot confirm: state unchanged, AC still active."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)

    with pytest.raises(ValueError):
        ConfirmProductionReadiness(workflow=article.articleworkflow, user=article.correspondence_author).run()

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED
    assert AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK).exists()


@pytest.mark.django_db
def test_confirm_view_success_and_double_submit(assigned_article, fake_request, settings, client: Client):
    """The view keeps its messages; a second POST shows the error instead of raising."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    client.force_login(get_eo_user(article))

    response = client.post(_confirm_url(article), follow=True)
    assert response.status_code == 200
    assert "Article confirmed as ready for typesetter." in [str(m) for m in response.context["messages"]]
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    assert not AttentionCondition.objects.active().filter(
        article=article, code=ac_service.ACCESS_MODE_TO_CHECK
    ).exists()

    response = client.post(_confirm_url(article), follow=True)
    assert response.status_code == 200
    assert "This article cannot transition to Ready for Typesetter in its current state." in [
        str(m) for m in response.context["messages"]
    ]


@pytest.mark.django_db
def test_withdraw_from_accepted_resolves_ac(assigned_article, fake_request, settings):
    """Withdrawing a paper held in Accepted resolves the AC (existing resolve-all)."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    author = article.correspondence_author
    fake_request.user = author

    WithdrawPreprint(
        workflow=article.articleworkflow,
        request=fake_request,
        form_data={"notification_subject": "Test subject", "notification_body": "Test body"},
    ).run()

    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.WITHDRAWN

    assert not AttentionCondition.objects.active().filter(
        article=article, code=ac_service.ACCESS_MODE_TO_CHECK
    ).exists()
```

`WithdrawPreprint` is a dataclass with fields `workflow`, `request`, `form_data` (it reads `notification_subject` / `notification_body` from `form_data` and checks `request.user` is the correspondence author or owner). The existing withdraw tests go through `WithdrawPreprintForm` (`test_logic.py`, around line 4301); calling the logic class directly is equivalent for this assertion.

If `response.context["messages"]` is `None` on the redirected page, read messages with `from django.contrib.messages import get_messages` and `list(get_messages(response.wsgi_request))` instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py -v`
Expected: collection ERROR `ImportError: cannot import name 'ConfirmProductionReadiness'`.

- [ ] **Step 3: Add the logic class**

In `logic__production.py`, right after the `VerifyProductionRequirements` class:

```python
@dataclasses.dataclass
class ConfirmProductionReadiness:
    """EO manually confirms that an accepted article is ready for the typesetter.

    Used for journals where the acceptance checks hold the article in ACCEPTED
    pending an out-of-band confirmation by the EO (e.g. JCAP TA papers).
    """

    workflow: ArticleWorkflow
    user: Account

    def _check_conditions(self) -> bool:
        """Check that the user can move the article to READY_FOR_TYPESETTER."""
        return has_transition_perm(self.workflow.system_verifies_production_requirements, self.user)

    def run(self) -> ArticleWorkflow:
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("This article cannot transition to Ready for Typesetter in its current state.")
            self.workflow.system_verifies_production_requirements()
            self.workflow.save()

            # -- Materialized AC updates --
            from . import ac_service

            # EO confirmed production readiness: the paper left ACCEPTED
            ac_service.resolve_for_role(self.workflow.article, "eo", ac_service.ACCESS_MODE_TO_CHECK)

        return self.workflow
```

- [ ] **Step 4: Delegate the view to the logic class**

In `views__production.py`, add `ConfirmProductionReadiness` to the `from .logic__production import (...)` block (alphabetical order), then replace `EOConfirmsProductionReady.post` with:

```python
    def post(self, request, *args, **kwargs):
        try:
            ConfirmProductionReadiness(workflow=self.object, user=request.user).run()
        except ValueError as e:
            messages.error(request, str(e))
            return HttpResponseRedirect(reverse("wjs_article_details", kwargs={"pk": self.object.pk}))
        messages.success(request, "Article confirmed as ready for typesetter.")
        return HttpResponseRedirect(reverse("wjs_article_details", kwargs={"pk": self.object.pk}))
```

If `has_transition_perm` is no longer used in `views__production.py`, remove it from the `from django_fsm import ...` import (flake8 will flag it).

- [ ] **Step 5: Run the tests to verify they pass**

Run the same command as Step 2. Expected: 7 passed.

- [ ] **Step 6: Commit (only if per-change commits were chosen)**

```bash
git add wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/views__production.py wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py
git commit -m "feat(wjs_review): resolve Access mode to check on EO production confirmation"
```

---

### Task 5: Regression run and lint

**Files:** none new.

- [ ] **Step 1: Run the AC, workflow and production test modules**

```bash
cd /home/andrea/Devel/sissa/janeway/src
python -m pytest -p no:freezegun -n7 \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions.py \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_rfp.py \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_workflow.py \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_logic.py \
  ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py -q
```

Expected: all pass. Report any failure verbatim; do not "fix" unrelated failures without asking.

- [ ] **Step 2: Run pre-commit on the changed files**

```bash
cd /home/andrea/Devel/sissa/wjs-profile-project
pre-commit run --files wjs/plugins/wjs_review/ac_service.py wjs/plugins/wjs_review/logic.py \
  wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/views__production.py \
  wjs/plugins/wjs_review/migrations/0018_resolve_stale_appeal_acs.py \
  wjs/plugins/wjs_review/tests/test_attentionconditions.py \
  wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py
```

Expected: all hooks pass (re-run once if black/isort reformat files).
