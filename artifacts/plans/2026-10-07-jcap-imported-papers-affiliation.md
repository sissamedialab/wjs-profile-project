# JCAP imported papers affiliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hold imported JCAP papers (populated `preprintid`) in Accepted until the EO releases them, without ever blocking the wjapp import, and let the EO set the paper affiliation from the `ArticleSubmission` advanced admin.

**Architecture:** In wjs-profile-project the JCAP acceptance check is renamed and gains an OR on "imported"; two small helpers in `wjs/jcom_profile/import_logic.py` lift the hold when the replayed wjapp history goes past acceptance (through the normal FSM transition, exactly like today's automatic path: zip sent to IOP, timeline message logged) and tidy up at the end of the import. In wjs-submission-project the existing `ArticleSubmissionAdmin` / `ArticleSubmissionAdminForm` expose `affiliation`.

**Tech Stack:** Django 5.2, Janeway 1.8, django-fsm, pytest / pytest-django.

**Spec:** `artifacts/specs/2026-10-07-jcap-imported-papers-affiliation-design.md` (wjs-profile-project)

## Global Constraints

- Documentation language: **English** (docstrings, comments, artifacts). Commit messages, branch names, MR: English.
- Branch in both repos: `feature/issue-3131-imported-papers-affiliation`, at the latest `wjs-develop` (already checked out). Changes stay in the working tree (no commits during implementation).
- JCAP only: the check is configured only under `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS["JCAP"]`.
- "Imported" means `bool(article.get_identifier("preprintid"))`.
- New check name: `jcap_production_readiness_not_confirmed`, dotted path `plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed`.
- The settings `jcap_ta_pending_subject` / `jcap_ta_pending_body` keep their names and values (no copy changes).
- The import release behaves like today's automatic path: it runs `system_verifies_production_requirements` (which sends the zip to IOP) and logs the "ready for typesetter" timeline message with the system as actor.
- Saving the admin form never recomputes the access mode. No help text on the affiliation field.
- No migrations, no new dependencies.

## Review Focus

- `TYP_TAKES_CHARGE.run()` and the command's end-of-import call are wiring only, not covered by tests (the importer needs a live wjapp DB): check the call sites are where the plan says, and that `finalize_production_readiness_hold` runs after the PUBLISHED/REJECTED forcing.
- A JCAP paper imported with history "accept → typesetter takes in charge": must end in TYPESETTER_SELECTED with no active `ACCESS_MODE_TO_CHECK`, sent to IOP once (as today). Pinned in Task 2 at helper level.
- A paper published in wjapp with an incomplete history (forced to PUBLISHED, no `TYP_TAKES_CHARGE`): no active `ACCESS_MODE_TO_CHECK` left. Pinned in Task 2.
- A deployment still configured with the old dotted path: acceptance of a JCAP paper fails. Not testable here; it is a "Required action" in the MR.
- Stored affiliation that no longer belongs to the correspondence author: other fields must still be savable. Pinned in Task 3.

## Running tests

From `janeway/src` (plugins are symlinked to the repos; `pytest_freezegun` is broken on Python 3.13):

```bash
cd /home/andrea/Devel/sissa/janeway/src
python -m pytest -p no:freezegun <relative path to test>::<test> -v
```

---

### Task 1: Rename the check and hold imported papers (wjs-profile-project)

**Files:**
- Modify: `wjs/plugins/wjs_review/events/checks_after_acceptance.py` (function at lines 22-71)
- Modify: `wjs/defaults/settings.py:216`
- Create: `wjs/plugins/wjs_review/tests/test_checks_after_acceptance.py`

**Interfaces:**
- Consumes: `plugins.wjs_review.tests.conftest._accept_article(fake_request, article, cleanup_side_effects=True)`; fixtures `assigned_article`, `fake_request`, `settings`; `wjs.jcom_profile.utils.get_eo_user`.
- Produces: `jcap_production_readiness_not_confirmed(article: Article) -> bool`; `render_production_readiness_subject(article: Article) -> str` (public, used by Task 2); private `_is_ta(article) -> bool`, `_is_imported(article) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `wjs/plugins/wjs_review/tests/test_checks_after_acceptance.py`:

```python
"""Checks run on just-accepted articles: JCAP TA and imported papers (specs#2591, specs#3131)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from identifiers.models import Identifier
from plugins.wjs_review import ac_service
from plugins.wjs_review.events.checks_after_acceptance import (
    jcap_production_readiness_not_confirmed,
    render_production_readiness_subject,
)
from plugins.wjs_review.models import ArticleWorkflow, AttentionCondition, Message, WjsEditorAssignment
from plugins.wjs_submission.models import AccessMode, ArticleSubmission
from plugins.wjs_submission.settings import OA_CODE_TA
from submission.models import Article

from wjs.jcom_profile.utils import get_eo_user

from .conftest import _accept_article

JCAP_CHECK = "plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed"


def _set_ta(article: Article) -> None:
    """Give the article the transformative-agreement access mode."""
    access_mode = AccessMode.objects.filter(code=OA_CODE_TA).first() or AccessMode.objects.create(
        name="Transformative agreement", code=OA_CODE_TA
    )
    submission_data = article.submission_data
    submission_data.access_mode = access_mode
    submission_data.save()


def _set_preprintid(article: Article, value: str = "JCAP_001P_1025") -> None:
    """Mark the article as imported from wjapp."""
    Identifier.objects.create(identifier=value, article=article, id_type="preprintid", enabled=True)


def _jcap_messages(article: Article):
    """Messages about this article sent by the JCAP check."""
    return Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.pk,
        subject=render_production_readiness_subject(article),
    )


@pytest.mark.django_db
def test_ta_paper_is_held(assigned_article):
    """A TA paper is held and the EO is notified (specs#2591)."""
    _set_ta(assigned_article)
    assert jcap_production_readiness_not_confirmed(assigned_article) is False, "TA paper must be held"
    messages = _jcap_messages(assigned_article)
    assert messages.count() == 1, "EO must receive one JCAP message"
    assert get_eo_user(assigned_article) in messages.first().recipients.all(), "The message must go to the EO"


@pytest.mark.django_db
def test_imported_paper_is_held(assigned_article):
    """An imported (non-TA) paper is held and the EO is notified (specs#3131)."""
    _set_preprintid(assigned_article)
    assert jcap_production_readiness_not_confirmed(assigned_article) is False, "Imported paper must be held"
    assert _jcap_messages(assigned_article).count() == 1, "EO must receive one JCAP message"


@pytest.mark.django_db
def test_imported_ta_paper_gets_one_message(assigned_article):
    """An imported TA paper is held with a single JCAP message: one condition, not two checks."""
    _set_ta(assigned_article)
    _set_preprintid(assigned_article)
    assert jcap_production_readiness_not_confirmed(assigned_article) is False, "Imported TA paper must be held"
    assert _jcap_messages(assigned_article).count() == 1, "Exactly one JCAP message expected"


@pytest.mark.django_db
def test_imported_paper_without_submission_data_is_held(assigned_article):
    """A missing ArticleSubmission row must not let an imported paper through."""
    _set_preprintid(assigned_article)
    ArticleSubmission.objects.filter(article=assigned_article).delete()
    article = Article.objects.get(pk=assigned_article.pk)
    assert jcap_production_readiness_not_confirmed(article) is False, "Imported paper without submission data held"


@pytest.mark.django_db
def test_native_non_ta_paper_passes(assigned_article):
    """A native (not imported) non-TA paper passes, without messages."""
    assert jcap_production_readiness_not_confirmed(assigned_article) is True, "Native non-TA paper must pass"
    assert not _jcap_messages(assigned_article).exists(), "No JCAP message expected"


@pytest.mark.django_db
def test_paper_without_submission_data_and_preprintid_passes(assigned_article):
    """No access mode information and no preprintid: the paper passes."""
    ArticleSubmission.objects.filter(article=assigned_article).delete()
    article = Article.objects.get(pk=assigned_article.pk)
    assert jcap_production_readiness_not_confirmed(article) is True, "Paper with no submission data must pass"


@pytest.mark.django_db
def test_empty_preprintid_is_not_imported(assigned_article):
    """An empty preprintid value is not a populated preprint ID: the paper passes."""
    _set_preprintid(assigned_article, value="")
    assert jcap_production_readiness_not_confirmed(assigned_article) is True, "Empty preprintid must not hold"


@pytest.mark.django_db
def test_check_configured_for_jcap_only(settings):
    """Only JCAP runs the check, under its new name: imported JCOM/JCOMAL papers are not held."""
    checks = settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS
    assert JCAP_CHECK in checks["JCAP"], "JCAP must run the check under its new dotted path"
    for journal_code in (None, "JCOM", "JCOMAL"):
        assert JCAP_CHECK not in checks[journal_code], f"{journal_code} must not run the JCAP check"


@pytest.mark.django_db
def test_accepting_imported_paper_holds_it_with_ac(assigned_article, fake_request: HttpRequest, settings):
    """End to end: accepting an imported paper leaves it in Accepted with the EO AC."""
    _set_preprintid(assigned_article)
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {assigned_article.journal.code: [JCAP_CHECK]}
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor
    _accept_article(fake_request, assigned_article)
    assigned_article.refresh_from_db()

    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, (
        "Imported paper must stay in Accepted"
    )
    assert (
        AttentionCondition.objects.active()
        .filter(article=assigned_article, user=get_eo_user(assigned_article), code=ac_service.ACCESS_MODE_TO_CHECK)
        .exists()
    ), "EO must get the 'Access mode to check' AC"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_checks_after_acceptance.py -v`
Expected: collection ERROR `ImportError: cannot import name 'jcap_production_readiness_not_confirmed'` (the new names do not exist yet).

- [ ] **Step 3: Implement**

In `wjs/plugins/wjs_review/events/checks_after_acceptance.py`, replace the whole `jcap_ta_not_yet_confirmed` function (from its `def` to its final `return False`) with:

```python
def _is_ta(article: submission_models.Article) -> bool:
    """Return whether the article's access mode is the transformative-agreement one."""
    try:
        access_mode = article.submission_data.access_mode
    except ObjectDoesNotExist:
        return False
    return access_mode is not None and access_mode.code == OA_CODE_TA


def _is_imported(article: submission_models.Article) -> bool:
    """Return whether the article was imported from wjapp, i.e. it has a populated preprintid."""
    return bool(article.get_identifier("preprintid"))


def _render_setting(setting_name: str, article: submission_models.Article) -> str:
    """Render a wjs_review setting with the article in the context."""
    return render_template(
        get_setting(setting_group_name="wjs_review", setting_name=setting_name, journal=article.journal).processed_value,
        {"article": article},
    )


def render_production_readiness_subject(article: submission_models.Article) -> str:
    """Return the subject of the message sent to the EO when an article is held in ACCEPTED.

    Also used by the wjapp importer to find that message (specs#3131).
    """
    return _render_setting("jcap_ta_pending_subject", article)


def jcap_production_readiness_not_confirmed(article: submission_models.Article) -> bool:
    """Block automatic transition to READY_FOR_TYPESETTER for JCAP TA and imported articles.

    Blocks if the article's access mode is TA (OA_CODE_TA, specs#2591) or if the article was
    imported from wjapp (populated preprintid, specs#3131): imported articles have no affiliation
    and the EO must set it before production. In both cases the EO must manually confirm
    production readiness via the article page. Returns False and notifies EO; otherwise returns True.
    """
    if not (_is_ta(article) or _is_imported(article)):
        return True

    # Local imports to avoid circular import: wjs_review.models is not fully
    # initialized when this module is loaded (e.g. via import_string for always_pass).
    from .. import communication_utils
    from ..models import Message

    communication_utils.log_operation(
        article=article,
        message_subject=render_production_readiness_subject(article),
        message_body=_render_setting("jcap_ta_pending_body", article),
        actor=None,
        recipients=[get_eo_user(article)],
        verbosity=Message.MessageVerbosity.FULL,
    )
    return False
```

In `wjs/defaults/settings.py`, line 216:

```python
    "JCAP": ("plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed",),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_checks_after_acceptance.py -v`
Expected: 9 passed.

Also: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_attentionconditions_accepted.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_ready_for_typesetter_timeline.py ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_workflow.py -q`
Expected: all pass.

- [ ] **Step 5: Lint**

From `/home/andrea/Devel/sissa/wjs-profile-project`: `pre-commit run --files wjs/plugins/wjs_review/events/checks_after_acceptance.py wjs/defaults/settings.py wjs/plugins/wjs_review/tests/test_checks_after_acceptance.py`
Expected: pass (re-run if a hook reformats).

---

### Task 2: Never block the import (wjs-profile-project)

**Files:**
- Modify: `wjs/jcom_profile/import_logic.py` (imports at lines 24 and 51-57; new helpers after `mark_all_messages_read`, line ~302; `TYP_TAKES_CHARGE.run()`, line ~3980)
- Modify: `wjs/jcom_profile/management/commands/import_articles_from_wjapp.py` (after the published-state forcing, before the reminders cleanup, line ~715)
- Create: `wjs/plugins/wjs_review/tests/test_import_production_readiness_hold.py`

**Interfaces:**
- Consumes: `render_production_readiness_subject(article) -> str` (Task 1); `ac_service.resolve_all_for_article(article, codes=[...])`; `ac_service.ACCESS_MODE_TO_CHECK`; `import_logic.mark_all_messages_read(article)`; fixtures `assigned_article`, `fake_request`, `settings`, `typesetter`, `monkeypatch`.
- Produces: `import_logic.release_production_readiness_hold(article) -> bool`, `import_logic.finalize_production_readiness_hold(article) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `wjs/plugins/wjs_review/tests/test_import_production_readiness_hold.py`:

```python
"""wjapp import and the JCAP production-readiness hold (specs#3131)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from identifiers.models import Identifier
from plugins.wjs_review import ac_service
from plugins.wjs_review.events.checks_after_acceptance import render_production_readiness_subject
from plugins.wjs_review.logic__production import AssignTypesetter, SendProductionXMLToPublisher
from plugins.wjs_review.models import (
    ArticleWorkflow,
    AttentionCondition,
    Message,
    MessageRecipients,
    WjsEditorAssignment,
)
from submission.models import Article

from wjs.jcom_profile import import_logic

from .conftest import _accept_article

JCAP_CHECK = "plugins.wjs_review.events.checks_after_acceptance.jcap_production_readiness_not_confirmed"
READY_FOR_TYPESETTER_SUBJECT = "Paper ready for typesetter"


@pytest.fixture
def publisher_sends(monkeypatch) -> list:
    """Record every attempt to send the production zip to the publisher."""
    calls = []

    def fake_run(self, **kwargs):
        calls.append(self.articleworkflow.article.pk)
        return self.articleworkflow

    monkeypatch.setattr(SendProductionXMLToPublisher, "run", fake_run)
    return calls


def _held_imported_article(article: Article, fake_request, settings) -> Article:
    """Accept an imported paper with the JCAP check configured: it is held in ACCEPTED."""
    Identifier.objects.create(identifier="JCAP_001P_1025", article=article, id_type="preprintid", enabled=True)
    settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {article.journal.code: [JCAP_CHECK]}
    fake_request.user = WjsEditorAssignment.objects.get_current(article).editor
    # Keep the messages logged at acceptance: the finalize tests assert on them.
    _accept_article(fake_request, article, cleanup_side_effects=False)
    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.ACCEPTED, "precondition: paper held"
    return article


def _active_hold_ac(article: Article):
    return AttentionCondition.objects.active().filter(article=article, code=ac_service.ACCESS_MODE_TO_CHECK)


def _messages(article: Article, subject: str):
    return Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article), object_id=article.pk, subject=subject
    )


@pytest.mark.django_db
def test_release_moves_held_paper_to_ready_for_typesetter(assigned_article, fake_request, settings, publisher_sends):
    """The history goes on after acceptance: the held paper is let through like on the automatic path."""
    article = _held_imported_article(assigned_article, fake_request, settings)

    assert import_logic.release_production_readiness_hold(article) is True, "A held paper must be released"

    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER, "Must reach RFT"
    assert not _active_hold_ac(article).exists(), "The hold AC must be resolved"
    assert publisher_sends == [article.pk], "The production zip must be sent once, as on the automatic path"
    assert _messages(article, READY_FOR_TYPESETTER_SUBJECT).count() == 1, "One ready-for-typesetter timeline message"


@pytest.mark.django_db
def test_release_lets_typesetter_take_in_charge(assigned_article, fake_request, settings, typesetter, publisher_sends):
    """After the release, the replayed TYP_TAKES_CHARGE (AssignTypesetter) succeeds."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    import_logic.release_production_readiness_hold(article)

    fake_request.user = typesetter
    AssignTypesetter(article=article, typesetter=typesetter, request=fake_request).run()

    article.refresh_from_db()
    assert article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED, (
        "The replay must go on into production"
    )


@pytest.mark.django_db
def test_release_does_nothing_when_not_accepted(assigned_article, publisher_sends):
    """A paper that is not in ACCEPTED is left alone."""
    state_before = assigned_article.articleworkflow.state

    assert import_logic.release_production_readiness_hold(assigned_article) is False, "Nothing to release"

    assigned_article.refresh_from_db()
    assert assigned_article.articleworkflow.state == state_before, "State must not change"
    assert publisher_sends == [], "Nothing must be sent"
    assert not _messages(assigned_article, READY_FOR_TYPESETTER_SUBJECT).exists(), "No timeline message"


@pytest.mark.django_db
def test_finalize_keeps_held_paper_message_unread(assigned_article, fake_request, settings):
    """History stopped at acceptance: the JCAP message is unread for the EO and the AC stays active."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    import_logic.mark_all_messages_read(article)

    import_logic.finalize_production_readiness_hold(article)

    jcap_messages = _messages(article, render_production_readiness_subject(article))
    assert jcap_messages.exists(), "precondition: the JCAP message was sent"
    assert not jcap_messages.filter(read_by_eo=True).exists(), "JCAP message must be unread for the EO"
    assert not MessageRecipients.objects.filter(message__in=jcap_messages, read=True).exists(), (
        "JCAP message must be unread for its recipients"
    )
    other_messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article), object_id=article.pk
    ).exclude(pk__in=jcap_messages)
    assert not other_messages.filter(read_by_eo=False).exists(), "Other messages must stay read"
    assert _active_hold_ac(article).exists(), "The hold AC must stay active"


@pytest.mark.django_db
def test_finalize_resolves_ac_when_paper_moved_on(assigned_article, fake_request, settings):
    """A paper forced to PUBLISHED by the import (incomplete history) keeps no hold AC."""
    article = _held_imported_article(assigned_article, fake_request, settings)
    workflow = article.articleworkflow
    workflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    workflow.save()

    import_logic.finalize_production_readiness_hold(article)

    assert not _active_hold_ac(article).exists(), "The hold AC must be resolved"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_import_production_readiness_hold.py -v`
Expected: 5 FAILED with `AttributeError: module 'wjs.jcom_profile.import_logic' has no attribute 'release_production_readiness_hold'` (or `finalize_production_readiness_hold`).

- [ ] **Step 3: Implement the helpers**

In `wjs/jcom_profile/import_logic.py`:

- line 24: `from plugins.wjs_review import ac_service, communication_utils`
- add `VerifyProductionRequirements` to the existing `from plugins.wjs_review.logic__production import (...)` block (alphabetical order)
- after the `from plugins.wjs_review.logic__production import (...)` block add:
  `from plugins.wjs_review.events.checks_after_acceptance import render_production_readiness_subject`
- right after `mark_all_messages_read()` add:

```python
def release_production_readiness_hold(article: submission_models.Article) -> bool:
    """Let through an article held in ACCEPTED by the production-readiness check (specs#3131).

    The replayed wjapp acceptance holds every imported JCAP paper in ACCEPTED; when the wjapp history
    goes on, the paper had already left that state in wjapp and the import must not stop. The release
    follows the automatic path of VerifyProductionRequirements: FSM transition (which sends the
    production zip to the publisher) and "ready for typesetter" timeline message.

    :return: True if the article was released, False if it was not in ACCEPTED.
    """
    workflow = article.articleworkflow
    if workflow.state != ArticleWorkflow.ReviewStates.ACCEPTED:
        return False
    workflow.system_verifies_production_requirements()
    workflow.save()
    VerifyProductionRequirements(workflow).log_ready_for_typesetter()
    ac_service.resolve_all_for_article(article, codes=[ac_service.ACCESS_MODE_TO_CHECK])
    logger.warning(f"Released {article.id} from ACCEPTED to READY_FOR_TYPESETTER: the wjapp history goes on")
    return True


def finalize_production_readiness_hold(article: submission_models.Article) -> None:
    """Settle the production-readiness hold once the import of an article is complete (specs#3131).

    An article still in ACCEPTED is held for the EO: the message asking to confirm production readiness
    is flagged unread again (the import marks every message read). In any other state nothing of the
    hold must be left behind, so its attention condition is resolved.
    """
    if article.articleworkflow.state != ArticleWorkflow.ReviewStates.ACCEPTED:
        ac_service.resolve_all_for_article(article, codes=[ac_service.ACCESS_MODE_TO_CHECK])
        return
    held_messages = Message.objects.filter(
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
        subject=render_production_readiness_subject(article),
    )
    MessageRecipients.objects.filter(message__in=held_messages).update(read=False)
    held_messages.update(read_by_eo=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/plugins/wjs_review/tests/test_import_production_readiness_hold.py -v`
Expected: 5 passed.

- [ ] **Step 5: Wire the helpers into the importer**

In `import_logic.py`, `TYP_TAKES_CHARGE.run()`, right after the `noop_cases` early return and before `typesetter_cod = self.action["agentCod"]`:

```python
        # specs#3131: the replayed acceptance may have held the paper in ACCEPTED; the wjapp history
        # goes on, so let it through before the typesetter takes it in charge.
        release_production_readiness_hold(self.article)
```

In `import_articles_from_wjapp.py`, after the `if publicationid:` block that forces the PUBLISHED stage/state and before the `if ArticleWorkflow.objects.filter(article=article).exists():` block that disables reminders:

```python
        # specs#3131: a paper whose wjapp history stopped at acceptance stays held for the EO;
        # any other paper must keep nothing of the hold.
        if ArticleWorkflow.objects.filter(article=article).exists():
            import_logic.finalize_production_readiness_hold(article)
```

Check the module still imports: `python -c "import django; django.setup()"` is not available standalone, so re-run the Step 4 command (it imports `import_logic`) and run
`python -m pytest -p no:freezegun ../../wjs-profile-project/wjs/jcom_profile/tests/test_import_command.py -q`
Expected: all pass (module and command import cleanly).

- [ ] **Step 6: Lint**

From `/home/andrea/Devel/sissa/wjs-profile-project`: `pre-commit run --files wjs/jcom_profile/import_logic.py wjs/jcom_profile/management/commands/import_articles_from_wjapp.py wjs/plugins/wjs_review/tests/test_import_production_readiness_hold.py`
Expected: pass.

---

### Task 3: Affiliation in the ArticleSubmission advanced admin (wjs-submission-project)

**Files:**
- Modify: `wjs/plugins/wjs_submission/advanced_admin/advanced_admin.py` (`ArticleSubmissionAdmin.fields`, line ~149)
- Modify: `wjs/plugins/wjs_submission/advanced_admin/forms.py` (imports, class docstring, `Meta.fields` line 62, `__init__`)
- Create: `tests/test_admin_affiliation.py`

**Interfaces:**
- Consumes: fixtures `article`, `author`, `coauthor` (`tests/conftest.py`: every account gets one primary `ControlledAffiliation`).
- Produces: `ArticleSubmissionAdminForm` with an optional `affiliation` field; `_get_affiliation_queryset(article) -> QuerySet[ControlledAffiliation]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_affiliation.py`:

```python
"""Tests for the affiliation field of the ArticleSubmission advanced admin (specs#3131)."""

import pytest
from core.models import ControlledAffiliation
from plugins.wjs_submission.advanced_admin.advanced_admin import ArticleSubmissionAdmin
from plugins.wjs_submission.advanced_admin.forms import ArticleSubmissionAdminForm
from plugins.wjs_submission.models import ArticleSubmission


def _submission(article) -> ArticleSubmission:
    submission, __ = ArticleSubmission.objects.get_or_create(article=article)
    return submission


def _form_data(submission: ArticleSubmission, affiliation: ControlledAffiliation | None) -> dict:
    """Bound data for the admin form, leaving every other field as it is."""
    return {
        "cover_letter_file": "",
        "access_mode": submission.access_mode_id or "",
        "special_request": submission.special_request,
        "affiliation": affiliation.pk if affiliation else "",
    }


@pytest.mark.django_db
def test_affiliation_is_an_admin_field():
    """The EO sees the affiliation in the ArticleSubmission advanced admin."""
    assert "affiliation" in ArticleSubmissionAdmin.fields, "affiliation must be editable in the admin"


@pytest.mark.django_db
def test_affiliation_choices_are_correspondence_author_affiliations(article, author, coauthor):
    """Choices are limited to the correspondence author's affiliations."""
    form = ArticleSubmissionAdminForm(instance=_submission(article))
    choices = set(form.fields["affiliation"].queryset)
    assert choices == set(author.affiliations), "Only the correspondence author's affiliations"
    assert not choices & set(coauthor.affiliations), "Other accounts' affiliations must not be offered"


@pytest.mark.django_db
def test_other_account_affiliation_is_rejected(article, coauthor):
    """An affiliation of another account cannot be saved."""
    submission = _submission(article)
    form = ArticleSubmissionAdminForm(data=_form_data(submission, coauthor.affiliations.first()), instance=submission)
    assert not form.is_valid(), "Another account's affiliation must be rejected"
    assert "affiliation" in form.errors, "The error must be on the affiliation field"


@pytest.mark.django_db
def test_saving_sets_affiliation_and_keeps_access_mode(article, author):
    """Saving stores the affiliation and does not touch access mode, licence and copyright."""
    submission = _submission(article)
    access_mode_before = submission.access_mode_id
    license_before = article.license_id
    rights_before = article.rights
    affiliation = author.affiliations.first()

    form = ArticleSubmissionAdminForm(data=_form_data(submission, affiliation), instance=submission)
    assert form.is_valid(), form.errors
    form.save()

    submission.refresh_from_db()
    article.refresh_from_db()
    assert submission.affiliation == affiliation, "The affiliation must be stored"
    assert submission.access_mode_id == access_mode_before, "Access mode must not be recomputed"
    assert article.license_id == license_before, "Licence must not change"
    assert article.rights == rights_before, "Copyright must not change"


@pytest.mark.django_db
def test_stored_affiliation_outside_author_stays_valid(article, coauthor):
    """A stored affiliation no longer belonging to the correspondence author does not block saving."""
    submission = _submission(article)
    stale_affiliation = coauthor.affiliations.first()
    submission.affiliation = stale_affiliation
    submission.save()

    form = ArticleSubmissionAdminForm(data=_form_data(submission, stale_affiliation), instance=submission)
    assert stale_affiliation in form.fields["affiliation"].queryset, "Stored affiliation must stay selectable"
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_form_valid_without_correspondence_author(article):
    """With no correspondence author there are no choices, and the form still validates."""
    article.correspondence_author = None
    article.save()
    submission = _submission(article)

    form = ArticleSubmissionAdminForm(data=_form_data(submission, None), instance=submission)
    assert not form.fields["affiliation"].queryset.exists(), "No affiliation to offer"
    assert form.is_valid(), form.errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -p no:freezegun ../../wjs-submission-project/tests/test_admin_affiliation.py -v`
Expected: 6 FAILED: `test_affiliation_is_an_admin_field` on its assertion, the others with `KeyError: 'affiliation'` or because the affiliation is not saved / another account's affiliation is accepted.

- [ ] **Step 3: Implement**

`advanced_admin.py`, `ArticleSubmissionAdmin.fields`: insert `"affiliation",` right after `"access_mode",`.

`forms.py`:
- imports: `from core.models import ControlledAffiliation` and `from django.db.models import Q`;
- class docstring, last paragraph: `The ``affiliation`` field is limited to the correspondence author's affiliations, plus the stored one.`;
- `Meta.fields`:

```python
        fields = [
            "cover_letter_file",
            "access_mode",
            "affiliation",
            "license_override",
            "rights_override",
            "special_request",
        ]
```

- in `__init__`, right after `article = self.instance.article`:
  `self.fields["affiliation"].queryset = self._get_affiliation_queryset(article)`
- new method, before `save()`:

```python
    def _get_affiliation_queryset(self, article):
        """
        Return the affiliations the EO can choose for the article.

        Like submission step 4, these are the correspondence author's affiliations. The stored
        affiliation is always included, so that an affiliation left from a previous correspondence
        author does not make the whole form invalid.
        """
        choices = Q(pk=self.instance.affiliation_id) if self.instance.affiliation_id else Q(pk__in=[])
        if article.correspondence_author_id:
            choices |= Q(account_id=article.correspondence_author_id)
        return ControlledAffiliation.objects.filter(choices)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -p no:freezegun ../../wjs-submission-project/tests/test_admin_affiliation.py ../../wjs-submission-project/tests/test_admin_access_mode.py -v`
Expected: all pass.

- [ ] **Step 5: Lint**

From `/home/andrea/Devel/sissa/wjs-submission-project`: `pre-commit run --files wjs/plugins/wjs_submission/advanced_admin/advanced_admin.py wjs/plugins/wjs_submission/advanced_admin/forms.py tests/test_admin_affiliation.py`
Expected: pass.
