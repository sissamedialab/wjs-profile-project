# Send production-ready article metadata XML to the publisher — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `ArticleWorkflow.system_verifies_production_requirements` fires, generate the article's production metadata XML, hand it to a per-journal publisher-delivery function (JCAP only for now), and log a message to EO — doing nothing at all for any other journal.

**Architecture:** The `@transition`-decorated `system_verifies_production_requirements` method (currently an empty `pass` body) delegates to a new `SendProductionXMLToPublisher` business-logic class in `logic__production.py`. That class gates on a per-journal dict setting (`WJS_REVIEW_XML_SEND_FUNCTIONS`, dynamic-import pattern, no default fallback) before doing anything; if the journal isn't a key, it returns immediately. If it is, it fetches the XML via the already-existing `metadata_export.service.serialize_article_to_metadata_xml`, hands it to the configured per-journal send function (a JCAP-only stub for now), and logs a `communication_utils.log_operation` message to EO. Both existing callers of the transition (`VerifyProductionRequirements`'s automatic path, `EOConfirmsProductionReady`'s manual EO-override view) are untouched — neither needs to know this happens.

**Tech Stack:** Django, django-fsm, pytest / pytest-django, this repo's existing `metadata_export` package (branch `feature/metadata-export-dto-mappers`).

**Spec:** `docs/superpowers/specs/2026-09-14-send-production-xml-to-publisher-design.md`

## Global Constraints

- Double quotes only, 4-space indent, 119-char line length (black), docstrings imperative/capitalized per `.claude/rules/code-style-python.md` and `.claude/rules/code-style-django.md`.
- `settings.WJS_REVIEW_XML_SEND_FUNCTIONS` must have **no `None` fallback key** — a missing journal code means "not supported," not "use a default."
- `SendProductionXMLToPublisher.run()` must do nothing at all (no XML generation, no send call, no message, no exception) when the journal isn't configured.
- Run tests from `janeway/src` (`/home/yakky/Projects/projects/sissa-1.8/janeway/src`), pointing at this worktree by relative path: `pytest --reuse-db path/to/test.py::test_name -v` run against `../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/...`. If imports resolve to a different `wjs-profile-project` checkout, run `pip install -e .[test]` from inside `wjs-profile-project-export-dto` first (see `.claude/rules/tests.md`).
- Run `pre-commit run --files <changed files>` before every commit.

---

### Task 1: JCAP publisher XML-send stub

**Files:**
- Create: `wjs/plugins/wjs_review/metadata_export/publishers.py`
- Modify: `wjs/plugins/wjs_review/tests/test_metadata_export.py` (import line + one new test)

**Interfaces:**
- Produces: `send_xml_to_jcap(article: submission.models.Article, xml: str) -> None`. Later tasks reference it only by dotted path, `"plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap"`, never by direct import.

- [ ] **Step 1: Write the failing test**

In `wjs/plugins/wjs_review/tests/test_metadata_export.py`, change the existing import line:

```python
from plugins.wjs_review.metadata_export import formatters, mappers, service
```

to:

```python
from plugins.wjs_review.metadata_export import formatters, mappers, publishers, service
```

Then add this test at the end of the file:

```python
def test_send_xml_to_jcap_is_a_noop_stub():
    """No transport is implemented yet: the stub must not raise and must return nothing."""
    assert publishers.send_xml_to_jcap(article=None, xml="<article/>") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `janeway/src`): `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py::test_send_xml_to_jcap_is_a_noop_stub -v`
Expected: FAIL — `ImportError: cannot import name 'publishers'` (module doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `wjs/plugins/wjs_review/metadata_export/publishers.py`:

```python
"""Per-journal functions that deliver an article's production metadata XML to its publisher.

Each function's signature is ``(article: submission.models.Article, xml: str) -> None``. A
function is referenced by dotted path from ``settings.WJS_REVIEW_XML_SEND_FUNCTIONS`` and resolved
via ``django.utils.module_loading.import_string`` -- the same per-journal dynamic-import pattern
used by ``WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`` and its siblings in ``wjs/defaults/settings.py``.
"""

from submission.models import Article


def send_xml_to_jcap(article: Article, xml: str) -> None:
    """Send an article's production metadata XML to JCAP.

    Stub: no transport implemented yet.
    """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_metadata_export.py::test_send_xml_to_jcap_is_a_noop_stub -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files wjs/plugins/wjs_review/metadata_export/publishers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git add wjs/plugins/wjs_review/metadata_export/publishers.py wjs/plugins/wjs_review/tests/test_metadata_export.py
git commit -m "feat(metadata-export): add JCAP production-XML send stub"
```

---

### Task 2: `WJS_REVIEW_XML_SEND_FUNCTIONS` setting, EO notification setting pair, and `SendProductionXMLToPublisher`

**Files:**
- Modify: `wjs/defaults/settings.py:187` (insert after the `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` block, before the `WJS_ARTICLE_PUBLISHED_SOCIAL_NOTIFICATION_EMAILS` comment)
- Modify: `wjs/plugins/wjs_review/plugin_settings.py:556` (insert a new nested function after `jcap_ta_pending_notification`, before `def hijack_notification_message()`) and `wjs/plugins/wjs_review/plugin_settings.py:2175` (append a registration call at the end of the `csv_writer.write_settings(...)` list inside `set_default_plugin_settings`)
- Modify: `wjs/plugins/wjs_review/logic__production.py:27` (typing import), `logic__production.py:81` (new import), `logic__production.py:154` (insert new class after `VerifyProductionRequirements`)
- Modify: `wjs/plugins/wjs_review/tests/test_production.py:38-45` (import) and append two new tests

**Interfaces:**
- Consumes: `send_xml_to_jcap` from Task 1 — referenced only via the `WJS_REVIEW_XML_SEND_FUNCTIONS` setting string, never imported directly; `serialize_article_to_metadata_xml(article) -> str` from the existing `wjs/plugins/wjs_review/metadata_export/service.py`.
- Produces: `SendProductionXMLToPublisher(articleworkflow: ArticleWorkflow).run() -> ArticleWorkflow`. Task 3 calls this exact constructor and method.

- [ ] **Step 1: Write the failing tests**

In `wjs/plugins/wjs_review/tests/test_production.py`, change:

```python
from ..logic__production import (
    AttachGalleys,
    BeginPublication,
    FinishPublication,
    HandleDownloadRevisionFiles,
    TypesettedFilesUpload,
    TypesetterTestsGalleyGeneration,
)
```

to:

```python
from ..logic__production import (
    AttachGalleys,
    BeginPublication,
    FinishPublication,
    HandleDownloadRevisionFiles,
    SendProductionXMLToPublisher,
    TypesettedFilesUpload,
    TypesetterTestsGalleyGeneration,
)
```

Then add these two tests at the end of the file:

```python
@pytest.mark.django_db
def test_send_production_xml_to_publisher_noop_for_unsupported_journal(accepted_article: Article):
    """No entry in WJS_REVIEW_XML_SEND_FUNCTIONS for the article's journal: nothing happens."""
    workflow = accepted_article.articleworkflow

    with (
        mock.patch("plugins.wjs_review.logic__production.serialize_article_to_metadata_xml") as mock_serialize,
        mock.patch("plugins.wjs_review.logic__production.communication_utils.log_operation") as mock_log,
    ):
        result = SendProductionXMLToPublisher(articleworkflow=workflow).run()

    assert result == workflow
    mock_serialize.assert_not_called()
    mock_log.assert_not_called()


@pytest.mark.django_db
def test_send_production_xml_to_publisher_sends_xml_and_logs_message(
    accepted_article: Article,
    settings,
):
    """A configured journal: XML is generated, handed to the send function, and logged to EO."""
    settings.WJS_REVIEW_XML_SEND_FUNCTIONS = {
        accepted_article.journal.code: "plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap",
    }
    workflow = accepted_article.articleworkflow

    with (
        mock.patch("plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap") as mock_send,
        mock.patch("plugins.wjs_review.logic__production.communication_utils.log_operation") as mock_log,
    ):
        result = SendProductionXMLToPublisher(articleworkflow=workflow).run()

    assert result == workflow
    mock_send.assert_called_once()
    called_article, called_xml = mock_send.call_args.args
    assert called_article == accepted_article
    assert called_xml.strip().startswith("<?xml")
    mock_log.assert_called_once_with(
        article=accepted_article,
        message_subject=mock.ANY,
        message_body=mock.ANY,
        actor=None,
        recipients=[get_eo_user(accepted_article)],
        verbosity=Message.MessageVerbosity.FULL,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_production.py -k test_send_production_xml_to_publisher -v`
Expected: FAIL — `ImportError: cannot import name 'SendProductionXMLToPublisher'`.

- [ ] **Step 3: Write minimal implementation**

In `wjs/defaults/settings.py`, insert immediately after the `WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS` block (after its closing `}` at line 187):

```python

# Per-journal functions that send an article's production metadata XML to the publisher.
# Journals with no entry here have no publisher integration: nothing happens (no XML generated,
# no message logged) -- there is no sensible default.
WJS_REVIEW_XML_SEND_FUNCTIONS = {
    "JCAP": "plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap",
}
```

In `wjs/plugins/wjs_review/plugin_settings.py`, insert this nested function immediately after `jcap_ta_pending_notification`'s `return setting_1, setting_2` (line 556) and before `def hijack_notification_message():`:

```python
    def xml_sent_to_publisher_notification() -> tuple[SettingValue, ...]:
        xml_sent_subject_setting: SettingParams = {
            "name": "xml_sent_to_publisher_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for XML sent to publisher notification to EO"),
            "description": _(
                "Subject of the message sent to EO when an article's production metadata XML "
                "has been sent to the publisher.",
            ),
            "is_translatable": False,
        }
        xml_sent_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Metadata XML sent to publisher for article {{ article.pk }}",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            xml_sent_subject_setting,
            xml_sent_subject_setting_value,
            xml_sent_subject_setting["name"],
            force=force,
        )
        xml_sent_body_setting: SettingParams = {
            "name": "xml_sent_to_publisher_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body for XML sent to publisher notification to EO"),
            "description": _(
                "Body of the message sent to EO when an article's production metadata XML "
                "has been sent to the publisher.",
            ),
            "is_translatable": False,
        }
        xml_sent_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": (
                "The production metadata XML for article {{ article.pk }} ({{ article.title }}) "
                "has been sent to the publisher."
            ),
            "translations": {},
        }
        setting_2 = create_customization_setting(
            xml_sent_body_setting,
            xml_sent_body_setting_value,
            xml_sent_body_setting["name"],
            force=force,
        )
        return setting_1, setting_2

```

Then, inside `set_default_plugin_settings`'s `with export_to_csv_manager("wjs_review") as csv_writer:` block, append a new line at the end of the existing `csv_writer.write_settings(...)` sequence (after `csv_writer.write_settings(expected_galleys())`):

```python
        csv_writer.write_settings(xml_sent_to_publisher_notification())
```

In `wjs/plugins/wjs_review/logic__production.py`, change the typing import (line 27):

```python
from typing import Optional, Tuple
```

to:

```python
from typing import Callable, Optional, Tuple
```

Add a new import right after `from . import communication_utils` (line 81):

```python
from . import communication_utils
from .metadata_export.service import serialize_article_to_metadata_xml
```

Insert this new class immediately after `VerifyProductionRequirements`'s `run()` method (after line 154, before the `# https://gitlab.sissamedialab.it/wjs/specs/-/issues/667` comment):

```python
@dataclasses.dataclass
class SendProductionXMLToPublisher:
    """Generate an accepted article's production metadata XML and send it to the publisher.

    No-op for any journal without a configured send function in
    ``settings.WJS_REVIEW_XML_SEND_FUNCTIONS``: there is no sensible default publisher
    integration, so a journal must be a key in that setting for anything (XML generation,
    sending, message logging) to happen at all.
    """

    articleworkflow: ArticleWorkflow

    def _get_send_function(self) -> Optional[Callable[[Article, str], None]]:
        """Return the configured per-journal XML-send function, or None if unsupported."""
        journal_code = self.articleworkflow.article.journal.code
        function_path = settings.WJS_REVIEW_XML_SEND_FUNCTIONS.get(journal_code)
        if function_path is None:
            return None
        return import_string(function_path)

    def _log_operation(self):
        """Log that the production XML was sent to the publisher."""
        context = {"article": self.articleworkflow.article}
        journal = self.articleworkflow.article.journal
        message_subject = render_template(
            get_setting(
                setting_group_name="wjs_review",
                setting_name="xml_sent_to_publisher_subject",
                journal=journal,
            ).processed_value,
            context,
        )
        message_body = render_template(
            get_setting(
                setting_group_name="wjs_review",
                setting_name="xml_sent_to_publisher_body",
                journal=journal,
            ).processed_value,
            context,
        )
        communication_utils.log_operation(
            article=self.articleworkflow.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[get_eo_user(self.articleworkflow.article)],
            verbosity=Message.MessageVerbosity.FULL,
        )

    def run(self) -> ArticleWorkflow:
        """Send the article's production metadata XML to the publisher, if the journal is configured for it."""
        send_function = self._get_send_function()
        if send_function is None:
            return self.articleworkflow
        with transaction.atomic():
            xml = serialize_article_to_metadata_xml(self.articleworkflow.article)
            send_function(self.articleworkflow.article, xml)
            self._log_operation()
        return self.articleworkflow
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_production.py -k test_send_production_xml_to_publisher -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files wjs/defaults/settings.py wjs/plugins/wjs_review/plugin_settings.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_production.py
git add wjs/defaults/settings.py wjs/plugins/wjs_review/plugin_settings.py wjs/plugins/wjs_review/logic__production.py wjs/plugins/wjs_review/tests/test_production.py
git commit -m "feat(wjs_review): add SendProductionXMLToPublisher logic class"
```

---

### Task 3: Wire the transition to `SendProductionXMLToPublisher`

**Files:**
- Modify: `wjs/plugins/wjs_review/models.py:1201-1202`
- Modify: `wjs/plugins/wjs_review/tests/test_workflow.py` (imports + two new tests)

**Interfaces:**
- Consumes: `SendProductionXMLToPublisher(articleworkflow: ArticleWorkflow).run()` from Task 2.

- [ ] **Step 1: Write the failing tests**

In `wjs/plugins/wjs_review/tests/test_workflow.py`, change the import block:

```python
import pytest
from django.http import HttpRequest
from django.utils import timezone
from events import logic as events_logic
from identifiers import models as identifiers_models
from submission import models as submission_models
from utils import setting_handler

from wjs.jcom_profile.models import JCOMProfile

from ..events import ReviewEvent
from ..models import ArticleWorkflow, WjsEditorAssignment
from ..plugin_settings import STAGE
from .conftest import _accept_article
```

to:

```python
from unittest import mock

import pytest
from django.http import HttpRequest
from django.utils import timezone
from events import logic as events_logic
from identifiers import models as identifiers_models
from submission import models as submission_models
from utils import setting_handler

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import get_eo_user

from ..events import ReviewEvent
from ..models import ArticleWorkflow, Message, WjsEditorAssignment
from ..plugin_settings import STAGE
from .conftest import _accept_article
```

Then add these two tests at the end of the file:

```python
@pytest.mark.django_db
def test_accepted_workflow_sends_xml_to_publisher_when_journal_configured(
    assigned_article: submission_models.Article,
    fake_request: HttpRequest,
    director: JCOMProfile,
    settings,
):
    """Accepting an article whose journal has a publisher XML-send function configured sends the XML and logs to EO."""
    settings.WJS_REVIEW_XML_SEND_FUNCTIONS = {
        assigned_article.journal.code: "plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap",
    }
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor

    with (
        mock.patch("plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap") as mock_send,
        mock.patch("plugins.wjs_review.logic__production.communication_utils.log_operation") as mock_log,
    ):
        _accept_article(fake_request, assigned_article)

    assigned_article.refresh_from_db()
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    mock_send.assert_called_once()
    called_article, called_xml = mock_send.call_args.args
    assert called_article == assigned_article
    assert called_xml.strip().startswith("<?xml")
    mock_log.assert_called_once_with(
        article=assigned_article,
        message_subject=mock.ANY,
        message_body=mock.ANY,
        actor=None,
        recipients=[get_eo_user(assigned_article)],
        verbosity=Message.MessageVerbosity.FULL,
    )


@pytest.mark.django_db
def test_accepted_workflow_does_not_send_xml_when_journal_not_configured(
    assigned_article: submission_models.Article,
    fake_request: HttpRequest,
    director: JCOMProfile,
):
    """No entry in WJS_REVIEW_XML_SEND_FUNCTIONS for the article's journal: nothing happens."""
    fake_request.user = WjsEditorAssignment.objects.get_current(assigned_article).editor

    with mock.patch("plugins.wjs_review.logic__production.serialize_article_to_metadata_xml") as mock_serialize:
        _accept_article(fake_request, assigned_article)

    assigned_article.refresh_from_db()
    assert assigned_article.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    mock_serialize.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_workflow.py -k "sends_xml_to_publisher or does_not_send_xml" -v`
Expected: FAIL — `mock_send.assert_called_once()` raises `AssertionError: Expected 'send_xml_to_jcap' to have been called once. Called 0 times.` (the transition body is still `pass`, so nothing is dispatched yet).

- [ ] **Step 3: Write minimal implementation**

In `wjs/plugins/wjs_review/models.py`, change:

```python
    # system verifies production requirements
    @transition(
        field=state,
        source=ReviewStates.ACCEPTED,
        target=ReviewStates.READY_FOR_TYPESETTER,
        permission=permissions.has_eo_role_by_article,
        # TODO: conditions=[],
    )
    def system_verifies_production_requirements(self):
        pass
```

to:

```python
    # system verifies production requirements
    @transition(
        field=state,
        source=ReviewStates.ACCEPTED,
        target=ReviewStates.READY_FOR_TYPESETTER,
        permission=permissions.has_eo_role_by_article,
        # TODO: conditions=[],
    )
    def system_verifies_production_requirements(self):
        # Local import to avoid circular import: logic__production imports ArticleWorkflow from
        # this module.
        from .logic__production import SendProductionXMLToPublisher

        SendProductionXMLToPublisher(articleworkflow=self).run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_workflow.py -k "sends_xml_to_publisher or does_not_send_xml" -v`
Expected: PASS (2 tests)

Then run the full `test_workflow.py` and `test_production.py` files to confirm nothing else broke:

```bash
pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_workflow.py -v
pytest ../../wjs-profile-project-export-dto/wjs/plugins/wjs_review/tests/test_production.py -v
```
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/tests/test_workflow.py
git add wjs/plugins/wjs_review/models.py wjs/plugins/wjs_review/tests/test_workflow.py
git commit -m "feat(wjs_review): send production XML to publisher on system_verifies_production_requirements"
```
