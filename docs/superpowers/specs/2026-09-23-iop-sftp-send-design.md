# Send the production export zip to IOP over SFTP

## Context

`wjs/specs#2972` ("Send to IOP the zip generated at ready-for-typesetter") asks for the
real transport behind `metadata_export/publishers.py::send_zip_to_iop`, which today is a
stub:

```python
def send_zip_to_iop(article: Article, zip_bytes: bytes) -> None:
    """... Stub: no transport implemented yet. ..."""
```

It is called from `SendProductionXMLToPublisher.run()` (`logic__production.py`), itself
driven by `ArticleWorkflow.system_verifies_production_requirements` (see
`2026-09-14-send-production-xml-to-publisher-design.md`), whenever
`WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS` has an entry for the article's journal
(today: `"JCAP"` only).

The issue's own text specifies the shape:

- open an SFTP session;
- copy the file to `partner-sissa/jcap/accepted-articles/` (the flow this branch already
  drives) or `partner-sissa/jcap/final-files/` (a second flow with no caller yet);
- the function must be transport-only and flow-agnostic — a parameter selects the flow;
- on error, create an attention condition (label left open in the issue);
- credentials/server were "to be provided later" — as of `#2972`'s discussion, IOP's real
  SFTP credentials have since been received and verified, but **this change does not use
  them**. Child issues `#3159` (configure SSH/SFTP on `wjs-test`) and `#3160` (validate
  the first draft against our own SFTP server) exist precisely because there is no SFTP
  server to test against yet anywhere in our infrastructure. Server/credential
  provisioning for `wjs-test` is ansible/ops work done separately from this branch; this
  change only adds the Django-side settings that will point at it once it exists.

## Non-goals

- No real IOP host/credentials. Only `wjs-test` settings scaffolding (empty defaults —
  the instance's ansible-rendered settings override supplies real values once `#3159` is
  done).
- No `final-files` flow entrypoint — the underlying transport is flow-agnostic, but only
  `accepted-articles` has a caller today (matching current `send_zip_to_iop` usage). A
  future `send_final_files_to_iop` is a one-line wrapper on the same helper, added when a
  caller exists.
- No ansible/sshd/chroot changes for `wjs-test` (`#3159`) — out of scope, done separately.
- No retry/backoff policy beyond "fail once, flag it, let a human retry" (see *Error
  handling*).

## Design

### Settings

New per-journal dict in `wjs/defaults/settings.py`, alongside
`WJS_REVIEW_ACCEPTANCE_ZIP_SEND_FUNCTIONS`:

```python
# Per-journal SFTP endpoint for delivering the production export zip. Empty defaults —
# an instance's own settings override supplies real host/credentials once the target
# SFTP server exists (see wjs/specs#3159 for wjs-test's own server).
WJS_REVIEW_IOP_SFTP = {
    "JCAP": {
        "host": "",
        "port": 22,
        "username": "",
        "private_key_path": "",
        "remote_paths": {
            "accepted-articles": "partner-sissa/jcap/accepted-articles",
            "final-files": "partner-sissa/jcap/final-files",
        },
    },
}
```

Key-based auth (`private_key_path`, no password field) — matches an SFTP-only chroot'd
user account, the standard pattern for this kind of automated delivery, and avoids a
plaintext-password setting. `remote_paths` keys are the flow names used by the `flow`
parameter below.

### Transport helper (new `metadata_export/sftp.py`)

```python
class SFTPSendError(Exception):
    """Raised when delivering a file over SFTP fails for any reason."""


def _send_via_sftp(article: Article, zip_bytes: bytes, *, journal_code: str, flow: str) -> None:
    """Upload zip_bytes to the configured SFTP endpoint for (journal_code, flow).

    Uploads to a temporary remote name and renames to its final name only after the
    full write succeeds, so a downstream ingestion process never sees a partial file.
    Raises SFTPSendError (never paramiko's own exceptions) on any failure, wrapping the
    original as __cause__.
    """
```

Filename: `{ms_no}.zip`, matching IOP's own sample naming
(`JCAP_101P_0825.zip`) — `ms_no` comes from
`ArticleWorkflow.preprint_id` via the same `map_ms_no` mapper the XML already uses.
Temp name: `.{ms_no}.zip.part`, written first, then `SFTPClient.posix_rename`'d to the
final name.

`publishers.py::send_zip_to_iop` becomes a thin wrapper:

```python
def send_zip_to_iop(article: Article, zip_bytes: bytes) -> None:
    _send_via_sftp(article, zip_bytes, journal_code="JCAP", flow="accepted-articles")
```

`paramiko` is a new dependency (`setup.cfg`) — no SFTP library exists in this codebase
today.

### Error handling — reverses the "let it raise" precedent for this one step

`2026-09-14-send-production-xml-to-publisher-design.md`'s error-handling section says
XML generation and the send call "succeed or fail together" with the transition,
propagating any exception. That was written when `send_function` was a no-op stub; it
never anticipated real network I/O. This change narrows that policy: **XML
serialization and zip-building still let exceptions propagate** (a real bug, not a
transient condition) — **but `send_zip_to_iop` itself does not**. `SendProductionXMLToPublisher.run()`
catches `SFTPSendError` specifically, so a delivery failure never blocks the
`ACCEPTED → READY_FOR_TYPESETTER` transition. Blocking production over a transient
network/SFTP-server issue would be worse than flagging it for manual follow-up, which is
exactly what the issue asks for ("handle errors by creating an attention condition").

On failure: `ac_service.upsert_for_role(article, "eo", PRODUCTION_EXPORT_SEND_FAILED, message=str(exc), priority=PRODUCTION_EXPORT_SEND_FAILED_PRIORITY)`.
On the next successful send (retry, manual or automatic): `ac_service.resolve_for_role(article, "eo", PRODUCTION_EXPORT_SEND_FAILED)`.

`PRODUCTION_EXPORT_SEND_FAILED` is a new code constant in `ac_service.py` — this answers
the issue's "label yet to be determined". It is **event-driven only**, not registered in
`STATE_ROLE_AC_MAP`: that map drives the nightly re-evaluation of time-based/condition
ACs via per-code evaluator functions, which doesn't apply here — this AC is created and
resolved exclusively from `SendProductionXMLToPublisher.run()`, so it needs its own fixed
priority constant (`PRODUCTION_EXPORT_SEND_FAILED_PRIORITY`, alongside the existing
`HAS_UNREAD_MESSAGE_PRIORITY`-style constants), not a `STATE_ROLE_AC_MAP` entry.

### Transaction fix

`SendProductionXMLToPublisher.run()` currently wraps zip-building *and* the send call in
one `transaction.atomic()` block, with a `NOTE` comment already flagging this as wrong
once real transport lands (holds a DB transaction open across a network round-trip).
This change acts on that note:

```python
def run(self) -> ArticleWorkflow:
    send_function = self._get_send_function()
    if send_function is None:
        return self.articleworkflow
    article = self.articleworkflow.article
    zip_bytes = build_production_export_zip(article)  # DB reads only, no writes
    try:
        send_function(article, zip_bytes)
    except SFTPSendError as exc:
        ac_service.upsert_for_role(
            article, "eo", ac_service.PRODUCTION_EXPORT_SEND_FAILED,
            message=str(exc), priority=ac_service.PRODUCTION_EXPORT_SEND_FAILED_PRIORITY,
        )
        return self.articleworkflow
    with transaction.atomic():
        self._log_operation()
        ac_service.resolve_for_role(article, "eo", ac_service.PRODUCTION_EXPORT_SEND_FAILED)
    return self.articleworkflow
```

Only the final DB writes (message log + AC resolve) stay atomic; zip-building and the
network call are outside any transaction.

## Testing

- `sftp._send_via_sftp`: mock `paramiko.SSHClient`/`SFTPClient` (no real network in the
  suite). Cases: success (temp name written, then renamed); connection failure wrapped
  as `SFTPSendError`; write failure wrapped as `SFTPSendError`; unconfigured
  `(journal_code, flow)` raises `SFTPSendError` (or a clear config error) rather than a
  raw `KeyError`.
- `send_zip_to_iop`: delegates to `_send_via_sftp` with `journal_code="JCAP",
  flow="accepted-articles"`.
- `SendProductionXMLToPublisher.run()`: success path resolves any existing
  `PRODUCTION_EXPORT_SEND_FAILED` AC and logs the message; `SFTPSendError` path creates
  the AC for the EO role, does **not** raise, and the transition still completes; a
  non-`SFTPSendError` exception from zip-building still propagates unchanged (existing
  behavior, unaffected by this change).

## Open questions

- Real IOP host/credentials/key are out of scope here (see *Non-goals*) — filled in
  separately once available, no code change needed.
- `wjs-test`'s own SFTP server (`#3159`) is provisioned separately (ansible/ops); this
  branch only adds the settings shape it will populate.
