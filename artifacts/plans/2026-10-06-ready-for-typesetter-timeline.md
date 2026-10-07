# Ready-for-typesetter timeline message Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record a timeline message whenever a paper moves from `Accepted` to `Ready for typesetter`, both automatically (acceptance checks pass) and manually (EO confirms production readiness).

**Architecture:** A module-level helper `log_ready_for_typesetter()` in `wjs/plugins/wjs_review/logic__production.py` renders two new `wjs_review` settings (subject/body) and calls `communication_utils.log_operation()` with TIMELINE verbosity. `VerifyProductionRequirements.run()` (automatic) and `ConfirmProductionReadiness.run()` (manual) call it inside their existing atomic block, right after the FSM transition is saved.

**Tech Stack:** Django, django-fsm, Janeway settings (`utils.setting_handler`), pytest.

**Spec:** `artifacts/specs/2026-10-06-ready-for-typesetter-timeline-design.md`

## Global Constraints

- Message: `verbosity=Message.MessageVerbosity.TIMELINE`, `recipients=[get_eo_user(article)]`, `flag_as_read=True`, `flag_as_read_by_eo=True`.
- Actor: the user passed by the action (manual), `None` → system user (automatic).
- Settings names: `ready_for_typesetter_subject` (text), `ready_for_typesetter_body` (rich-text), group `wjs_review`, `is_translatable: False`.
- Template context: `{"article": article, "actor": <Account or None>}`.
- The helper only logs: it performs no permission/role check. Authorization stays in the action (`_check_conditions`), which runs before the helper is called.
- No backfill, no email.
- Tests run from `janeway/src`: `pytest ../../wjs-profile-project/<path>` (never the full suite unless needed).

## Review Focus

- Paper held in `Accepted` (checks fail): must not log a "ready for typesetter" message — test in Task 1.
- Refused / repeated confirmation: the action raises before the helper is called, so nothing is logged — guaranteed by call order, not by the helper; existing `test_attentionconditions_accepted.py` tests cover the refusal itself.
- Unread-message AC: the message must be read for the EO, so no `HAS_UNREAD_MESSAGE` AC — asserted in Task 1.
- Journal-level override of the settings: rendering uses `get_setting(..., journal=article.journal)` — exercised implicitly; no extra test.

---

### Task 1: Settings, helper and automatic path

**Files:**
- Modify: `wjs/plugins/wjs_review/plugin_settings.py` (new nested function after `jcap_ta_pending_notification`, plus registration in the CSV export list)
- Modify: `wjs/plugins/wjs_review/logic__production.py` (new helper before `VerifyProductionRequirements`; call in its `run()` else-branch)
- Create (test): `wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py`

**Interfaces:**
- Produces: `log_ready_for_typesetter(article: Article, actor: Account | None = None) -> Message` in `plugins.wjs_review.logic__production`.

- [ ] **Step 1: Write the failing tests**

Create `wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py`:

```python
"""Timeline message for the Accepted -> Ready for typesetter transition (specs#3178)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from plugins.wjs_review import ac_service
from plugins.wjs_review.communication_utils import get_system_user
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    Message,
    WjsEditorAssignment,
)
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _accept_article

HOLD_IN_ACCEPTED = "wjs_review.events.checks.always_reject"
PASS_TO_TYPESETTER = "wjs_review.events.checks_after_acceptance.always_pass"
SUBJECT = "Paper ready for typesetter"


def _accept_with_checks(article: Article, fake_request: HttpRequest, settings, check_function: str) -> Article:
    """Accept the article with the given acceptance check function configured for its journal."""
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: [check_function]}
    fake_request.user = WjsEditorAssignment.objects.get_current(article).editor
    _accept_article(fake_request, article)
    article.refresh_from_db()
    return article


def _ready_messages(article: Article):
    return Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.pk,
        subject=SUBJECT,
    )


@pytest.mark.django_db
def test_automatic_transition_logs_timeline_message(assigned_article, fake_request, settings):
    """Checks pass: the system moves the paper and logs one timeline message."""
    article = _accept_with_checks(assigned_article, fake_request, settings, PASS_TO_TYPESETTER)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER, "checks should pass"

    messages = _ready_messages(article)
    assert messages.count() == 1, "exactly one ready-for-typesetter message expected"
    message = messages.get()
    assert message.verbosity == Message.MessageVerbosity.TIMELINE, "message must be timeline-only"
    assert message.actor == get_system_user(article.journal), "automatic transition is done by the system"
    assert "verified" in message.body, "body should state the automatic verification"
    assert list(message.recipients.all()) == [get_eo_user(article)], "EO user is the only recipient"
    assert message.read_by_eo, "message must be flagged as read by EO"
    assert not AttentionCondition.objects.active().filter(
        article=article, user=get_eo_user(article), code=ac_service.HAS_UNREAD_MESSAGE
    ).exists(), "an informational message must not raise an unread-message AC for the EO"


@pytest.mark.django_db
def test_held_in_accepted_logs_no_ready_message(assigned_article, fake_request, settings):
    """Checks fail: the paper stays in Accepted and no ready-for-typesetter message is logged."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "checks should hold the paper"
    assert not _ready_messages(article).exists(), "no ready-for-typesetter message expected"
```


- [ ] **Step 2: Run tests to verify they fail**

Run (from `janeway/src`): `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py -v`
Expected: `test_automatic_transition_logs_timeline_message` FAILS ("exactly one ready-for-typesetter message expected", count 0); `test_held_in_accepted_logs_no_ready_message` PASSES (guard test).

- [ ] **Step 3: Add the settings**

In `plugin_settings.py`, inside `set_default_plugin_settings`, after `jcap_ta_pending_notification()`:

```python
    def ready_for_typesetter_message() -> tuple[SettingValue, ...]:
        subject_setting: SettingParams = {
            "name": "ready_for_typesetter_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject of the ready for typesetter timeline message"),
            "description": _(
                "Subject of the timeline message logged when an accepted article is moved to "
                "Ready for typesetter, automatically or by the EO confirming production readiness.",
            ),
            "is_translatable": False,
        }
        subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Paper ready for typesetter",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_setting,
            subject_setting_value,
            subject_setting["name"],
            force=force,
        )
        body_setting: SettingParams = {
            "name": "ready_for_typesetter_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body of the ready for typesetter timeline message"),
            "description": _(
                "Body of the timeline message logged when an accepted article is moved to "
                "Ready for typesetter. Context: article, actor (the user who confirmed "
                "production readiness, empty when the transition is automatic).",
            ),
            "is_translatable": False,
        }
        body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": (
                "{% if actor %}Production readiness confirmed by {{ actor.full_name }}."
                "{% else %}Production requirements verified by the system.{% endif %}"
                "<br><br>The paper is now ready for typesetter."
            ),
            "translations": {},
        }
        setting_2 = create_customization_setting(
            body_setting,
            body_setting_value,
            body_setting["name"],
            force=force,
        )
        return setting_1, setting_2
```

And in the export block, right after `csv_writer.write_settings(jcap_ta_pending_notification())`:

```python
        csv_writer.write_settings(ready_for_typesetter_message())
```

- [ ] **Step 4: Add the helper and call it from the automatic path**

In `logic__production.py`, add `render_template` to the existing `from wjs.jcom_profile.utils import (...)` block, then add before `VerifyProductionRequirements`:

```python
def log_ready_for_typesetter(article: Article, actor: Optional[Account] = None) -> Message:
    """Log on the timeline that the article moved from ACCEPTED to READY_FOR_TYPESETTER.

    Logging only: the caller is responsible for checking that the transition is allowed.

    :param article: the article that is now ready for typesetter
    :param actor: the user who confirmed production readiness; None when the system verified
        the production requirements automatically (the system user is then the actor)
    """
    context = {"article": article, "actor": actor}
    message_subject = render_template(
        get_setting(
            setting_group_name="wjs_review",
            setting_name="ready_for_typesetter_subject",
            journal=article.journal,
        ).processed_value,
        context,
    )
    message_body = render_template(
        get_setting(
            setting_group_name="wjs_review",
            setting_name="ready_for_typesetter_body",
            journal=article.journal,
        ).processed_value,
        context,
    )
    return communication_utils.log_operation(
        article=article,
        message_subject=message_subject,
        message_body=message_body,
        actor=actor,
        recipients=[get_eo_user(article)],
        verbosity=Message.MessageVerbosity.TIMELINE,
        flag_as_read=True,
        flag_as_read_by_eo=True,
    )
```

In `VerifyProductionRequirements.run()`, else-branch:

```python
            else:
                self.articleworkflow.system_verifies_production_requirements()
                self.articleworkflow.save()
                log_ready_for_typesetter(self.articleworkflow.article)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Run neighbouring tests**

Run: `pytest -n7 ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_workflow.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_production.py`
Expected: all PASS (watch for tests asserting exact message counts on accepted articles; if any fails, stop and report — it is a plan gap).

- [ ] **Step 7: Commit** (only if per-change commits were chosen in step 4 of the flow)

```bash
git add wjs/plugins/wjs_review/plugin_settings.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py
git commit -m "feat(wjs_review): log timeline message when paper is automatically ready for typesetter"
```

### Task 2: Manual path (EO confirms production readiness)

**Files:**
- Modify: `wjs/plugins/wjs_review/logic__production.py` (`ConfirmProductionReadiness.run()`)
- Test: `wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py`

**Interfaces:**
- Consumes: `log_ready_for_typesetter(article, actor)` from Task 1.

- [ ] **Step 1: Write the failing tests**

Append to `test_ready_for_typesetter_timeline.py` (add `from plugins.wjs_review.logic__production import ConfirmProductionReadiness` to the imports):

```python
@pytest.mark.django_db
def test_eo_confirmation_logs_timeline_message(assigned_article, fake_request, settings):
    """Manual confirmation: one timeline message, actor is the confirming user."""
    article = _accept_with_checks(assigned_article, fake_request, settings, HOLD_IN_ACCEPTED)
    eo = get_eo_user(article)

    ConfirmProductionReadiness(workflow=article.articleworkflow, user=eo).run()

    messages = _ready_messages(article)
    assert messages.count() == 1, "exactly one ready-for-typesetter message expected"
    message = messages.get()
    assert message.verbosity == Message.MessageVerbosity.TIMELINE, "message must be timeline-only"
    assert message.actor == eo, "actor must be the confirming user"
    assert f"confirmed by {eo.full_name()}" in message.body, "body should name the confirming user"

```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py -v`
Expected: `test_eo_confirmation_logs_timeline_message` FAILS (count 0); the Task 1 tests PASS.

- [ ] **Step 3: Implement**

In `ConfirmProductionReadiness.run()`:

```python
            self.workflow.system_verifies_production_requirements()
            self.workflow.save()
            log_ready_for_typesetter(self.workflow.article, actor=self.user)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint**

Run (from the repo root): `pre-commit run --files wjs/plugins/wjs_review/plugin_settings.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py`
Expected: all hooks pass.

- [ ] **Step 6: Commit** (only if per-change commits were chosen)

```bash
git add wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py
git commit -m "feat(wjs_review): log timeline message when EO confirms production readiness"
```

## Deployment note

The new settings are installed automatically on deploy (`run_customizations` → `link_plugins` → `install_plugins wjs_review` → `set_default_plugin_settings(force=True)`). Instances not deployed that way need `python manage.py setup_review_settings`, otherwise `get_setting` raises and the transition rolls back. Mention it in the MR description.
