# Send production-ready article metadata XML to the publisher

## Context

`ArticleWorkflow.system_verifies_production_requirements` (`models.py`) is the
`django-fsm` transition that moves an article `ACCEPTED → READY_FOR_TYPESETTER`. It
currently has an empty body — all side effects around it live in the two existing
call sites:

- `VerifyProductionRequirements` (`logic__production.py`) — the automatic path: runs
  per-journal checks (`WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`) and, if they pass,
  calls the transition and saves.
- `EOConfirmsProductionReady` (`views__production.py`) — the manual EO-override path
  (e.g. for JCAP TA articles blocked by the automatic check): calls the transition
  directly, no logic class, no form.

Separately, `wjs/plugins/wjs_review/metadata_export/` (this branch,
`feature/metadata-export-dto-mappers`) already builds a JCAP-style metadata XML
export for an accepted article via `serialize_article_to_metadata_xml(article)`
(`metadata_export/service.py`), today invoked only from a one-off management
command.

This change wires that XML export into the actual editorial workflow: once an
article is confirmed ready for the typesetter, its metadata XML should be generated
and handed off to the publisher, and the fact logged as an in-app message to EO.
Today only JCAP needs this; no other journal has a publisher integration to send to.

## Goal

When `system_verifies_production_requirements` fires (from either existing call
site), and the journal has a publisher XML-delivery function configured, generate
the article's metadata XML, hand it to that function, and log a message to EO
recording that it was sent. For every other journal, nothing beyond the transition
itself happens — no XML is generated, no message is logged, no exception is raised.

## Design

### Call stack

The transition method itself drives the new behavior, so neither existing caller
needs to change:

```python
# models.py
@transition(
    field=state,
    source=ReviewStates.ACCEPTED,
    target=ReviewStates.READY_FOR_TYPESETTER,
    permission=permissions.has_eo_role_by_article,
)
def system_verifies_production_requirements(self):
    from .logic__production import SendProductionXMLToPublisher

    SendProductionXMLToPublisher(articleworkflow=self).run()
```

The import is local to break the `models.py` ↔ `logic__production.py` circular
import (`logic__production.py` imports `ArticleWorkflow` from `models.py`), per the
"avoid local imports unless needed to solve a circular dependency" rule.

Both `VerifyProductionRequirements.run()` (automatic path, after its checks pass)
and `EOConfirmsProductionReady.post()` (manual path) keep calling
`system_verifies_production_requirements()` exactly as they do today — they are
deliberately kept unaware that XML delivery is or isn't happening underneath.

### `SendProductionXMLToPublisher` (new, `logic__production.py`)

```python
@dataclasses.dataclass
class SendProductionXMLToPublisher:
    """Generate the production-ready article's metadata XML and send it to the publisher.

    No-op for any journal without a configured send function: there is no sensible
    default publisher integration, so a journal must be a key in
    WJS_REVIEW_XML_SEND_FUNCTIONS for anything (XML generation, send, logging) to
    happen at all.
    """

    articleworkflow: ArticleWorkflow

    def _get_send_function(self) -> Optional[Callable[[Article, str], None]]:
        journal = self.articleworkflow.article.journal.code
        function_path = settings.WJS_REVIEW_XML_SEND_FUNCTIONS.get(journal)
        if function_path is None:
            return None
        return import_string(function_path)

    def _log_operation(self):
        context = {"article": self.articleworkflow.article}
        journal = self.articleworkflow.article.journal
        message_subject = render_template(
            get_setting("wjs_review", "xml_sent_to_publisher_subject", journal).processed_value,
            context,
        )
        message_body = render_template(
            get_setting("wjs_review", "xml_sent_to_publisher_body", journal).processed_value,
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
        send_function = self._get_send_function()
        if send_function is None:
            return self.articleworkflow
        with transaction.atomic():
            xml = serialize_article_to_metadata_xml(self.articleworkflow.article)
            send_function(self.articleworkflow.article, xml)
            self._log_operation()
        return self.articleworkflow
```

### Per-journal dispatch setting

`wjs/defaults/settings.py`, following the existing
`WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`-style registry, but **deliberately with
no `None` fallback key** — a missing journal code means "not supported," not "use a
default":

```python
# Per-journal functions that send an article's production metadata XML to the
# publisher. Journals with no entry here have no publisher integration: nothing
# happens (no XML generated, no message logged) — there is no sensible default.
WJS_REVIEW_XML_SEND_FUNCTIONS = {
    "JCAP": "plugins.wjs_review.metadata_export.publishers.send_xml_to_jcap",
}
```

### Stub send function

New module `wjs/plugins/wjs_review/metadata_export/publishers.py`:

```python
def send_xml_to_jcap(article: Article, xml: str) -> None:
    """Send an article's production metadata XML to JCAP.

    Stub: no transport implemented yet.
    """
```

### Notification setting pair

New pair in `plugin_settings.py`, following the `jcap_ta_pending_subject`/`_body`
convention (group `wjs_review`, `journal: None` default, Django-template body
against `{"article": article}`), registered in `set_default_plugin_settings`:

- `xml_sent_to_publisher_subject` (`types: "text"`)
- `xml_sent_to_publisher_body` (`types: "rich-text"`)

## Error handling

If `serialize_article_to_metadata_xml` or `send_function` raises, the exception
propagates out of `SendProductionXMLToPublisher.run()`, out of the transition
method, and out of the caller's own `transaction.atomic()` block (or, for
`EOConfirmsProductionReady`, prevents the subsequent `.save()`) — the state
transition and the XML send succeed or fail together. No new error handling is
introduced; this matches the "let it raise" precedent already used by
`metadata_export.mappers` (`UnsupportedArticleStageForExportError`).

## Testing

- `SendProductionXMLToPublisher`: journal with no entry in
  `WJS_REVIEW_XML_SEND_FUNCTIONS` → no XML generated, send function not called, no
  `Message` created, no exception. Journal `"JCAP"` → XML generated, stub called
  with `(article, xml)`, `Message` created with the rendered subject/body,
  `actor=None`, recipients `[eo_user]`.
- `ArticleWorkflow.system_verifies_production_requirements`: transitions state and
  delegates to `SendProductionXMLToPublisher` (mock it to isolate the transition
  test from XML generation).
- Existing `VerifyProductionRequirements` / `EOConfirmsProductionReady` tests:
  update as needed to mock `SendProductionXMLToPublisher`/XML generation so they
  don't depend on real template rendering or `pdfinfo`.

## Non-goals

- No real transport implementation for `send_xml_to_jcap` (stub only).
- No change to the automatic check functions
  (`WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`) or to the manual EO-override flow's
  UI.
- No support for any journal other than JCAP.
