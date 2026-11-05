# Publisher Green Light Before Publication — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an EO-toggled `production_flag_iop_ready` flag that, for JCAP only, blocks `BeginPublication` (and greys out "Publish") until the publisher has confirmed the paper.

**Architecture:** A boolean on `ArticleWorkflow` plus two model methods (`requires_publisher_confirmation()`, `publisher_confirmation_missing()`) are the single source of truth. A toggle logic class + POST view (mirroring `TogglePublishableFlag`) flips the flag; one shared `ArticleAction` exposes it in four production states; `BeginPublication.check_conditions()` and the "Publish" action's `disabled` callable consume the predicate. Journal scoping goes through a new setting.

**Tech Stack:** Django, django-fsm (`FSMField`), pytest + pytest-django, Janeway plugin `wjs_review`.

**Spec:** `artifacts/specs/2026-10-13-publisher-confirmation-gate-design.md`

> **Post-review amendment (final code review, 2026-10-15).** Tasks 1-4 were executed as written below; the final
> review then found that a plain toggle lets a stale "Revoke" button re-confirm the paper. Task 2's toggle was
> replaced by an explicit set: `SetPublisherConfirmedFlag(workflow, confirmed: bool)` /
> `SetPublisherConfirmedFlagView`, URL name `wjs_set_publisher_confirmed` reading `?confirmed=1|0` (built by the
> action's `custom_get_url`), refusing a request for the value already stored and saving with
> `update_fields=["production_flag_iop_ready"]`. The spec's *Set/revoke* section is authoritative; names below that
> say `Toggle…` / `wjs_toggle_publisher_confirmed` are historical.

## Global Constraints

- Flag name: `production_flag_iop_ready`; `BooleanField(default=False, verbose_name=_("The publisher confirmed that the paper can be published"))`.
- Setting: `WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = ["JCAP"]`.
- Toggle allowed only to EO, only in `READY_FOR_TYPESETTER`, `TYPESETTER_SELECTED`, `PROOFREADING`, `READY_FOR_PUBLICATION`, only for journals in the setting.
- Gate message (exact): `Please ensure that the publisher confirmed that this paper can be published (and click on the appropriate action)`.
- Action labels (exact): `Publisher confirmed paper is publishable` (flag False) / `Revoke publisher confirmation` (flag True).
- `ArticleWorkflow.can_be_set_rfp()` must NOT change.
- Importers are not touched.
- Documentation language: English.
- Line length / style: black + flake8 as configured by the repo pre-commit.

## How to run tests

Tests run from Janeway, not from this repo (see `.claude/rules/tests.md`):

```bash
cd /home/andrea/Devel/sissa/janeway/src
pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v
```

If the run fails with an unexpected `NoReverseMatch` or uses old code, the Janeway venv has a stale non-editable install: `pip install -e ../../wjs-profile-project[test]`.

All new tests live in a new focused file `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py` (the spec mentions `test_production.py`; a dedicated file keeps the feature's tests together and lets them use pytest-django's `settings` fixture, which `test_production.py` shadows with a module-level `from django.conf import settings`).

Test fixtures used (all existing, from `wjs/plugins/wjs_review/tests/conftest.py` and `wjs/jcom_profile/tests/conftest.py`): `assigned_to_typesetter_article` (state `TYPESETTER_SELECTED`), `rfp_article` (state `READY_FOR_PUBLICATION`, has issue, galleys and completed TA), `eo_user`, `typesetter`, `client`, `fake_request`, `settings`. The test journal code is `"JCOM"`, so tests opt the journal in with `settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [article.journal.code]` and opt out with `[]`.

## Review Focus

- Non-JCAP paper in RFP without the flag must still publish exactly as today — pinned in Task 4 (`test_begin_publication_ignores_flag_for_other_journals`, `test_publish_never_disabled_for_other_journals`).
- Revoking a confirmation must re-block publication — pinned in Task 4 (`test_revoking_confirmation_blocks_publication_again`).
- A forged/stale POST to `begin_publication` without the flag must leave the article untouched and show the message — pinned in Task 4 (`test_begin_publication_view_redirects_with_message`).
- Toggle on a paper already in `PUBLICATION_IN_PROGRESS`/`PUBLISHED` must be refused — pinned in Task 2 (`test_toggle_rejected_in_other_states` parametrization).
- A GET on the toggle URL must not change anything — pinned in Task 2 (`test_toggle_view_get_not_allowed`).

---

### Task 1: Flag, setting, model predicates, migration, admin

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py` (production flags block ≈ line 485-498; methods next to `can_be_set_rfp()` ≈ line 716)
- Create: `wjs/plugins/wjs_review/migrations/0021_articleworkflow_production_flag_iop_ready.py`
- Modify: `wjs/plugins/wjs_review/admin.py` (`fields` list ≈ line 101-104)
- Modify: `wjs/defaults/settings.py` (near `WJS_JOURNALS_WITH_ENGLISH_CONTENT`, ≈ line 552)
- Test: `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py` (new)

**Interfaces:**
- Produces:
  - `ArticleWorkflow.production_flag_iop_ready: bool`
  - `ArticleWorkflow.requires_publisher_confirmation(self) -> bool`
  - `ArticleWorkflow.publisher_confirmation_missing(self) -> bool`
  - `settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION: list[str]`

- [ ] **Step 1: Write the failing tests**

Create `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py`:

```python
"""Tests for the publisher green light before publication (wjs/specs#2914)."""

import pytest
from submission.models import Article


@pytest.mark.django_db
def test_requires_publisher_confirmation_follows_setting(assigned_to_typesetter_article: Article, settings):
    workflow = assigned_to_typesetter_article.articleworkflow
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    assert workflow.requires_publisher_confirmation(), "Journal listed in the setting must require confirmation"
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    assert not workflow.requires_publisher_confirmation(), "Journal not listed must not require confirmation"


@pytest.mark.django_db
def test_publisher_confirmation_missing(assigned_to_typesetter_article: Article, settings):
    workflow = assigned_to_typesetter_article.articleworkflow
    assert workflow.production_flag_iop_ready is False, "New flag must default to False"

    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    assert workflow.publisher_confirmation_missing(), "Required and not set: confirmation is missing"
    workflow.production_flag_iop_ready = True
    assert not workflow.publisher_confirmation_missing(), "Required and set: confirmation is not missing"

    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    workflow.production_flag_iop_ready = False
    assert not workflow.publisher_confirmation_missing(), "Not required: confirmation is never missing"


def test_default_setting_lists_only_jcap():
    from wjs.defaults import settings as wjs_settings

    assert wjs_settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION == ["JCAP"], "Only JCAP requires it by default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v`
Expected: FAIL — `AttributeError: 'ArticleWorkflow' object has no attribute 'requires_publisher_confirmation'` / `production_flag_iop_ready`, and `AttributeError` on the setting.

- [ ] **Step 3: Add the setting**

In `wjs/defaults/settings.py`, right after `WJS_JOURNALS_WITH_ENGLISH_CONTENT = ["JCOMAL"]`:

```python
# Journals whose publisher must give a green light before the EO can publish a paper
# (wjs/specs#2914): the EO records it with the "Publisher confirmed paper is publishable" action
# (ArticleWorkflow.production_flag_iop_ready) and BeginPublication refuses to start without it.
WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = ["JCAP"]
```

- [ ] **Step 4: Add the field and the methods**

In `wjs/plugins/wjs_review/models.py`, after `production_flag_no_checks_needed`:

```python
    production_flag_iop_ready = models.BooleanField(
        default=False,
        verbose_name=_("The publisher confirmed that the paper can be published"),
    )
```

Right after `can_be_set_rfp()`:

```python
    def requires_publisher_confirmation(self) -> bool:
        """Test if the article's journal needs the publisher's green light before publication."""
        return self.article.journal.code in settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION

    def publisher_confirmation_missing(self) -> bool:
        """Test if publication must wait for the publisher's green light.

        The flag only gates publication: it plays no role in :py:meth:`can_be_set_rfp`.
        """
        return self.requires_publisher_confirmation() and not self.production_flag_iop_ready
```

Check that `models.py` already imports `from django.conf import settings`; add it if missing.

- [ ] **Step 5: Write the migration**

Create `wjs/plugins/wjs_review/migrations/0021_articleworkflow_production_flag_iop_ready.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wjs_review", "0020_appeal_granted_system_actor"),
    ]

    operations = [
        migrations.AddField(
            model_name="articleworkflow",
            name="production_flag_iop_ready",
            field=models.BooleanField(
                default=False,
                verbose_name="The publisher confirmed that the paper can be published",
            ),
        ),
    ]
```

Then verify nothing else is pending for this field (from `janeway/src`):
`python manage.py makemigrations wjs_review --check --dry-run`
Expected: no change reported for `production_flag_iop_ready`. If unrelated translation-field changes appear, ignore them (do not commit them).

- [ ] **Step 6: Add the flag to the admin**

In `wjs/plugins/wjs_review/admin.py`, after `"production_flag_no_checks_needed",` in `fields`:

```python
        "production_flag_iop_ready",
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v`
Expected: 3 passed.

- [ ] **Step 8: Commit (only if per-change commits were chosen at flow step 4)**

```bash
git add wjs/defaults/settings.py wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/admin.py \
  wjs/plugins/wjs_review/migrations/0021_articleworkflow_production_flag_iop_ready.py \
  wjs/plugins/wjs_review/tests/test_publisher_confirmation.py
git commit -m "feat(production): add publisher confirmation flag (#2914)"
```

---

### Task 2: Toggle logic, view and URL

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py` (new class right after `TogglePublishableFlag`, ≈ line 1083)
- Modify: `wjs/plugins/wjs_review/views__production.py` (import + new view right after `TogglePublishableFlagView`, ≈ line 488)
- Modify: `wjs/plugins/wjs_review/urls.py` (import ≈ line 84; path right after `wjs_toggle_publishable`, ≈ line 295)
- Test: `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py`

**Interfaces:**
- Consumes: `ArticleWorkflow.requires_publisher_confirmation()`, `production_flag_iop_ready` (Task 1).
- Produces:
  - `logic__production.TogglePublisherConfirmedFlag(workflow: ArticleWorkflow)` with `.run() -> ArticleWorkflow` (raises `ValueError`)
  - `views__production.TogglePublisherConfirmedFlagView`
  - URL name `wjs_toggle_publisher_confirmed`, path `paper_publisher_confirmed/<int:pk>/`

- [ ] **Step 1: Write the failing tests**

Append to `test_publisher_confirmation.py` (merge the imports at the top of the file):

```python
from django.test.client import Client
from django.urls import reverse
from plugins.wjs_review.logic__production import TogglePublisherConfirmedFlag

from ..models import ArticleWorkflow

TOGGLE_STATES = [
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PROOFREADING,
    ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
]


def _set_state(article: Article, state: str) -> ArticleWorkflow:
    workflow = article.articleworkflow
    workflow.state = state
    workflow.save()
    return workflow


@pytest.mark.django_db
@pytest.mark.parametrize("state", TOGGLE_STATES)
def test_toggle_flips_flag_both_ways(assigned_to_typesetter_article: Article, settings, state):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, state)

    TogglePublisherConfirmedFlag(workflow=workflow).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is True, f"First toggle in {state} must set the flag"

    TogglePublisherConfirmedFlag(workflow=workflow).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, f"Second toggle in {state} must clear the flag"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [
        ArticleWorkflow.ReviewStates.ACCEPTED,
        ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS,
        ArticleWorkflow.ReviewStates.PUBLISHED,
    ],
)
def test_toggle_rejected_in_other_states(assigned_to_typesetter_article: Article, settings, state):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, state)

    with pytest.raises(ValueError):
        TogglePublisherConfirmedFlag(workflow=workflow).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, f"Flag must not change in {state}"


@pytest.mark.django_db
def test_toggle_rejected_for_journal_not_requiring_confirmation(assigned_to_typesetter_article: Article, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    workflow = assigned_to_typesetter_article.articleworkflow

    with pytest.raises(ValueError):
        TogglePublisherConfirmedFlag(workflow=workflow).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Flag must not change for a journal outside the setting"


@pytest.mark.django_db
def test_toggle_view_eo_can_toggle(assigned_to_typesetter_article: Article, eo_user, client: Client, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    client.force_login(eo_user)

    response = client.post(reverse("wjs_toggle_publisher_confirmed", kwargs={"pk": workflow.pk}))

    assert response.status_code == 302, "EO toggle must redirect"
    assert response.headers["Location"] == reverse("wjs_article_details", kwargs={"pk": workflow.pk})
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is True, "EO toggle must set the flag"


@pytest.mark.django_db
def test_toggle_view_forbidden_to_typesetter_and_author(
    assigned_to_typesetter_article: Article, typesetter, client: Client, settings
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    url = reverse("wjs_toggle_publisher_confirmed", kwargs={"pk": workflow.pk})

    for user in (typesetter, assigned_to_typesetter_article.correspondence_author):
        client.force_login(user)
        response = client.post(url)
        assert response.status_code == 403, f"{user} must not be allowed to toggle"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Forbidden requests must not change the flag"


@pytest.mark.django_db
def test_toggle_view_error_in_wrong_state_shows_message(
    assigned_to_typesetter_article: Article, eo_user, client: Client, settings
):
    from django.contrib.messages import get_messages

    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, ArticleWorkflow.ReviewStates.ACCEPTED)
    client.force_login(eo_user)

    response = client.post(reverse("wjs_toggle_publisher_confirmed", kwargs={"pk": workflow.pk}))

    assert response.status_code == 302, "Rejected toggle must redirect to the status page"
    assert [str(m) for m in get_messages(response.wsgi_request)], "Rejected toggle must show an error message"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "Rejected toggle must not change the flag"


@pytest.mark.django_db
def test_toggle_view_get_not_allowed(assigned_to_typesetter_article: Article, eo_user, client: Client, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    client.force_login(eo_user)

    response = client.get(reverse("wjs_toggle_publisher_confirmed", kwargs={"pk": workflow.pk}))

    assert response.status_code == 405, "Toggle is POST-only"
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False, "GET must not change the flag"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v`
Expected: collection error — `ImportError: cannot import name 'TogglePublisherConfirmedFlag'`.

- [ ] **Step 3: Implement the logic**

In `logic__production.py`, right after the `TogglePublishableFlag` class:

```python
@dataclasses.dataclass
class TogglePublisherConfirmedFlag:
    """EO records (or revokes) the publisher's green light for publication (wjs/specs#2914)."""

    workflow: ArticleWorkflow

    def _check_conditions(self) -> bool:
        return self.workflow.requires_publisher_confirmation() and self.workflow.state in [
            ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
            ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
            ArticleWorkflow.ReviewStates.PROOFREADING,
            ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
        ]

    def _toggle_flag(self):
        self.workflow.production_flag_iop_ready = not self.workflow.production_flag_iop_ready
        self.workflow.save()

    def run(self) -> ArticleWorkflow:
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("The publisher confirmation cannot be changed for this paper now.")
            self._toggle_flag()
        return self.workflow
```

- [ ] **Step 4: Implement the view**

In `views__production.py`, add `TogglePublisherConfirmedFlag,` to the `from .logic__production import (...)` block (alphabetical, after `TogglePublishableFlag,`). Right after `TogglePublishableFlagView`:

```python
class TogglePublisherConfirmedFlagView(AuthenticatedUserPassesTest, View):
    """EO toggles `production_flag_iop_ready`: the publisher confirmed that the paper can be published."""

    model = ArticleWorkflow
    http_method_names = ["post"]

    def load_initial(self, request, *args, **kwargs):
        super().load_initial(request, *args, **kwargs)
        self.object = self.model.objects.get(pk=self.kwargs["pk"])

    def test_func(self):
        """Only EO can record the publisher's confirmation."""
        return base_permissions.has_eo_role(self.request.user)

    def post(self, request, *args, **kwargs):
        redirect_url = reverse("wjs_article_details", kwargs={"pk": self.object.pk})
        try:
            TogglePublisherConfirmedFlag(workflow=self.object).run()
        except ValueError as e:
            messages.error(request=self.request, message=str(e))
        return HttpResponseRedirect(redirect_url)
```

Note: if `http_method_names = ["post"]` makes `AuthenticatedUserPassesTest.dispatch` run `test_func` before Django's 405 (so a GET by EO returns 403 instead of 405), keep the restriction and relax the test expectation to `in (403, 405)` — what matters is that GET never changes the flag. Report it if this happens.

- [ ] **Step 5: Wire the URL**

In `urls.py`, add `TogglePublisherConfirmedFlagView,` to the view imports (after `TogglePublishableFlagView,`) and, right after the `wjs_toggle_publishable` path:

```python
    path(
        "paper_publisher_confirmed/<int:pk>/",
        TogglePublisherConfirmedFlagView.as_view(),
        name="wjs_toggle_publisher_confirmed",
    ),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v`
Expected: all passed (3 from Task 1 + 4 + 3 + 1 + 4 = 15).

- [ ] **Step 7: Commit (only if per-change commits were chosen)**

```bash
git add wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/views__production.py \
  wjs/plugins/wjs_review/urls.py wjs/plugins/wjs_review/tests/test_publisher_confirmation.py
git commit -m "feat(production): let EO toggle the publisher confirmation (#2914)"
```

---

### Task 3: Shared toggle action in the four production states

**Files:**
- Modify: `wjs/plugins/wjs_review/conditions.py` (new function near `journal_requires_social_media_files`, ≈ line 676)
- Modify: `wjs/plugins/wjs_review/states.py` (label/confirm helpers after `get_publishable_confirm_text` ≈ line 372; shared action constant after the `ArticleAction` dataclass ≈ line 493; add to `ReadyForTypesetter`, `TypesetterSelected`, `Proofreading`, `ReadyForPublication`)
- Test: `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py`

**Interfaces:**
- Consumes: `ArticleWorkflow.requires_publisher_confirmation()`, `production_flag_iop_ready` (Task 1); URL name `wjs_toggle_publisher_confirmed` (Task 2).
- Produces:
  - `conditions.journal_requires_publisher_confirmation(workflow: ArticleWorkflow, user: Account) -> bool`
  - `states.get_publisher_confirmed_label(action, workflow, user) -> str`
  - `states.get_publisher_confirmed_confirm_text(action, workflow, user) -> str`
  - `states.PUBLISHER_CONFIRMED_ACTION: ArticleAction` with `name="toggle publisher confirmed flag"`

- [ ] **Step 1: Write the failing tests**

Append to `test_publisher_confirmation.py` (merge imports at the top):

```python
from plugins.wjs_review import states

PUBLISHER_ACTION_NAME = "toggle publisher confirmed flag"


def _available_action_names(workflow: ArticleWorkflow, user) -> list[str]:
    state_class = states.BaseState.get_state_class(workflow)
    return [action.name for action in state_class.article_actions if action.is_available(workflow, user)]


@pytest.mark.django_db
@pytest.mark.parametrize("state", TOGGLE_STATES)
def test_action_available_to_eo_in_production_states(
    assigned_to_typesetter_article: Article, eo_user, settings, state
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = _set_state(assigned_to_typesetter_article, state)
    assert PUBLISHER_ACTION_NAME in _available_action_names(workflow, eo_user), f"EO must see the action in {state}"


@pytest.mark.django_db
def test_action_not_available_to_typesetter(assigned_to_typesetter_article: Article, typesetter, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    assert PUBLISHER_ACTION_NAME not in _available_action_names(workflow, typesetter), "Typesetter must not see it"


@pytest.mark.django_db
def test_action_hidden_for_journal_not_requiring_confirmation(
    assigned_to_typesetter_article: Article, eo_user, settings
):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    workflow = assigned_to_typesetter_article.articleworkflow
    assert PUBLISHER_ACTION_NAME not in _available_action_names(workflow, eo_user), "Hidden outside the setting"


@pytest.mark.django_db
def test_action_label_and_confirm_follow_flag(assigned_to_typesetter_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [assigned_to_typesetter_article.journal.code]
    workflow = assigned_to_typesetter_article.articleworkflow
    action = states.BaseState.get_state_class(workflow).get_action_by_name(PUBLISHER_ACTION_NAME)

    as_dict = action.as_dict(workflow, eo_user)
    assert as_dict["label"] == "Publisher confirmed paper is publishable", "Label when flag is False"
    assert as_dict["url"] == reverse("wjs_toggle_publisher_confirmed", kwargs={"pk": workflow.pk})
    assert as_dict["is_post"] is True, "Toggle must be a POST action"
    unset_confirm = as_dict["confirm"]

    workflow.production_flag_iop_ready = True
    as_dict = action.as_dict(workflow, eo_user)
    assert as_dict["label"] == "Revoke publisher confirmation", "Label when flag is True"
    assert as_dict["confirm"] and as_dict["confirm"] != unset_confirm, "Confirm text must follow the flag"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v -k action`
Expected: FAIL — action name not in available actions; `get_action_by_name` returns `None` (`AttributeError: 'NoneType' object has no attribute 'as_dict'`).

- [ ] **Step 3: Add the condition wrapper**

In `conditions.py`, after `journal_requires_social_media_files`:

```python
def journal_requires_publisher_confirmation(workflow: ArticleWorkflow, user: Account) -> bool:
    """Check if the article's journal needs the publisher's green light before publication."""
    return workflow.requires_publisher_confirmation()
```

(`ArticleWorkflow` and `Account` are already imported in `conditions.py`; verify and add if not.)

- [ ] **Step 4: Add label/confirm helpers and the shared action**

In `states.py`, right after `get_publishable_confirm_text`:

```python
def get_publisher_confirmed_label(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return the label for the button that toggles the publisher's confirmation.

    The label describes the action that would be done, not the state of the article.
    """
    if workflow.production_flag_iop_ready:
        return _("Revoke publisher confirmation")
    return _("Publisher confirmed paper is publishable")


def get_publisher_confirmed_confirm_text(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return the confirmation text for the button that toggles the publisher's confirmation."""
    if workflow.production_flag_iop_ready:
        return _("Are you sure you want to revoke the publisher confirmation? The paper will not be publishable.")
    return _("Are you sure the publisher confirmed that this paper can be published?")
```

Right after the `ArticleAction` dataclass definition (before `class ReviewAssignmentAction`):

```python
# EO records the publisher's green light (wjs/specs#2914). Shared by every production state where it can be toggled.
PUBLISHER_CONFIRMED_ACTION = ArticleAction(
    permission=permissions.has_eo_role_by_article,
    condition=conditions.journal_requires_publisher_confirmation,
    name="toggle publisher confirmed flag",
    label="Publisher confirmed paper is publishable",
    view_name="wjs_toggle_publisher_confirmed",
    custom_get_label=get_publisher_confirmed_label,
    custom_get_confirm=get_publisher_confirmed_confirm_text,
    is_post=True,
)
```

Add `PUBLISHER_CONFIRMED_ACTION,` as the last element of the explicit `article_actions` tuple (just before `) + BaseState.article_actions`) of each of: `ReadyForTypesetter`, `TypesetterSelected`, `Proofreading`, `ReadyForPublication`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v`
Expected: all passed (15 + 4 + 1 + 1 + 1 = 22).

- [ ] **Step 6: Run the neighbouring action/state suites**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_actions.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_views_base.py -v`
Expected: same pass/fail set as on `wjs-develop` (compare with a baseline run on the base commit if anything fails).

- [ ] **Step 7: Commit (only if per-change commits were chosen)**

```bash
git add wjs/plugins/wjs_review/conditions.py wjs/plugins/wjs_review/states.py \
  wjs/plugins/wjs_review/tests/test_publisher_confirmation.py
git commit -m "feat(production): add publisher confirmation action to production states (#2914)"
```

---

### Task 4: Publication gate and disabled "Publish"

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py` (`BeginPublication.check_conditions`, ≈ line 1800-1809)
- Modify: `wjs/plugins/wjs_review/states.py` (new `publisher_confirmation_missing` callable near `galley_generation_in_progress` ≈ line 395; `disabled=` on the "begin publication" action of `ReadyForPublication`)
- Test: `wjs/plugins/wjs_review/tests/test_publisher_confirmation.py`

**Interfaces:**
- Consumes: `ArticleWorkflow.publisher_confirmation_missing()` (Task 1); `TogglePublisherConfirmedFlag` (Task 2).
- Produces: `states.publisher_confirmation_missing(action, workflow, user) -> bool`; `BeginPublication.check_conditions()` returns `(False, PUBLISHER_CONFIRMATION_MISSING_MESSAGE)` when missing.

- [ ] **Step 1: Write the failing tests**

Append to `test_publisher_confirmation.py` (merge imports at the top):

```python
from unittest import mock

from django.contrib.messages import get_messages
from django.http import HttpRequest
from plugins.wjs_review.logic__production import BeginPublication

GATE_MESSAGE = (
    "Please ensure that the publisher confirmed that this paper can be published "
    "(and click on the appropriate action)"
)


def _begin_publication(rfp_article: Article, eo_user, fake_request: HttpRequest) -> BeginPublication:
    return BeginPublication(workflow=rfp_article.articleworkflow, user=eo_user, request=fake_request)


@pytest.mark.django_db
def test_begin_publication_blocked_without_confirmation(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (False, GATE_MESSAGE)


@pytest.mark.django_db
def test_begin_publication_allowed_with_confirmation(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    rfp_article.articleworkflow.production_flag_iop_ready = True
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (True, None)


@pytest.mark.django_db
def test_begin_publication_ignores_flag_for_other_journals(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    assert rfp_article.articleworkflow.production_flag_iop_ready is False
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (True, None)


@pytest.mark.django_db
def test_revoking_confirmation_blocks_publication_again(rfp_article: Article, eo_user, fake_request, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    workflow.save()  # persist the RFP state set in memory by the fixture
    TogglePublisherConfirmedFlag(workflow=workflow).run()
    TogglePublisherConfirmedFlag(workflow=workflow).run()
    workflow.refresh_from_db()
    assert workflow.production_flag_iop_ready is False
    assert _begin_publication(rfp_article, eo_user, fake_request).check_conditions() == (False, GATE_MESSAGE)


@pytest.mark.django_db
def test_begin_publication_view_redirects_with_message(rfp_article: Article, eo_user, client: Client, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    workflow.save()
    client.force_login(eo_user)

    with mock.patch("plugins.wjs_review.logic__production.FinishPublication.run") as finish:
        response = client.post(reverse("wjs_review_begin_publication", kwargs={"pk": workflow.pk}))

    assert response.status_code == 302, "Blocked publication must redirect"
    assert response.headers["Location"] == reverse("wjs_article_details", kwargs={"pk": workflow.pk})
    assert any(GATE_MESSAGE in str(m) for m in get_messages(response.wsgi_request)), "Gate message must be shown"
    finish.assert_not_called()
    workflow.refresh_from_db()
    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION, "State must not change"


@pytest.mark.django_db
def test_publish_disabled_until_confirmed(rfp_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = [rfp_article.journal.code]
    workflow = rfp_article.articleworkflow
    action = states.ReadyForPublication.get_action_by_name("begin publication")

    assert action.as_dict(workflow, eo_user)["disabled"] is True, "Publish disabled without confirmation"
    workflow.production_flag_iop_ready = True
    assert action.as_dict(workflow, eo_user)["disabled"] is False, "Publish enabled once confirmed"


@pytest.mark.django_db
def test_publish_never_disabled_for_other_journals(rfp_article: Article, eo_user, settings):
    settings.WJS_JOURNALS_REQUIRING_PUBLISHER_CONFIRMATION = []
    action = states.ReadyForPublication.get_action_by_name("begin publication")
    assert action.as_dict(rfp_article.articleworkflow, eo_user)["disabled"] is False, "Never disabled outside setting"
```

Note: `states.ReadyForPublication` is the state class (not the `logic__production.ReadyForPublication` service); always reference it through the `states` module. If `get_action_by_name` is an instance method rather than a classmethod, use `states.BaseState.get_state_class(workflow).get_action_by_name(...)` as in Task 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v -k "publication or publish"`
Expected: FAIL — `check_conditions()` returns `(True, None)` where `(False, GATE_MESSAGE)` is expected; `disabled` is `None` instead of `True`/`False`; the view test sees no gate message.

- [ ] **Step 3: Add the gate to `BeginPublication`**

In `logic__production.py`, at module level near the other module constants (or just above `class BeginPublication`):

```python
PUBLISHER_CONFIRMATION_MISSING_MESSAGE = (
    "Please ensure that the publisher confirmed that this paper can be published "
    "(and click on the appropriate action)"
)
```

In `BeginPublication.check_conditions()`, after the `primary_issue is None` check and before `return (True, None)`:

```python
        if self.workflow.publisher_confirmation_missing():
            return (False, PUBLISHER_CONFIRMATION_MISSING_MESSAGE)
```

- [ ] **Step 4: Disable "Publish" while the confirmation is missing**

In `states.py`, right after `galley_generation_in_progress`:

```python
def publisher_confirmation_missing(action: "ArticleAction", workflow: "ArticleWorkflow", user: Account):
    """Return true if the publication must wait for the publisher's green light."""
    return workflow.publisher_confirmation_missing()
```

In `ReadyForPublication.article_actions`, on the `name="begin publication"` action add:

```python
            disabled=publisher_confirmation_missing,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_publisher_confirmation.py -v`
Expected: all passed (22 + 7 = 29).

- [ ] **Step 6: Run the publication-related suites**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_views_base.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_rfp.py`
Expected: same pass/fail set as on `wjs-develop`. Test journal is `JCOM`, not in the default setting, so existing publication tests must be unaffected. Any new failure is a regression to investigate.

- [ ] **Step 7: Lint**

Run (from the repo root): `pre-commit run --files wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/states.py wjs/plugins/wjs_review/views__production.py wjs/plugins/wjs_review/urls.py wjs/plugins/wjs_review/conditions.py wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/admin.py wjs/defaults/settings.py wjs/plugins/wjs_review/migrations/0021_articleworkflow_production_flag_iop_ready.py wjs/plugins/wjs_review/tests/test_publisher_confirmation.py`
Expected: all hooks pass (re-run once if black/isort reformatted files).

- [ ] **Step 8: Commit (only if per-change commits were chosen)**

```bash
git add wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/states.py \
  wjs/plugins/wjs_review/tests/test_publisher_confirmation.py
git commit -m "feat(production): block publication until publisher confirms (#2914)"
```
