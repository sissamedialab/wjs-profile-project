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
  SFTP credentials have since been received and verified. **(Revised)** IOP's public
  endpoint data — host, username and server host key — is now in the default settings
  (see *Settings*); the secret credentials still are not. Child issues `#3159` (configure SSH/SFTP on `wjs-test`) and `#3160` (validate
  the first draft against our own SFTP server) exist precisely because there is no SFTP
  server to test against yet anywhere in our infrastructure. Server/credential
  provisioning for `wjs-test` is ansible/ops work done separately from this branch; this
  change only adds the Django-side settings that will point at it once it exists.

## Non-goals

- No real IOP credentials in the repo. **(Revised)** The default settings carry IOP's
  non-secret endpoint data (host, username, host key); the secret part
  (`private_key_path`/`password`) stays empty and is supplied by the instance's
  ansible-rendered settings override. `wjs-test` overrides host/username/host key too,
  to point at its own server once `#3159` is done.
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
# Per-journal SFTP endpoint for delivering the production export zip. JCAP points at
# IOP's real server: host, username and host_key are not secrets, so they are set here;
# the credentials are supplied by an instance's own settings override. An instance that
# must not deliver to IOP (e.g. wjs-test, wjs/specs#3159) overrides host/username/host_key
# as well.
WJS_REVIEW_IOP_SFTP = {
    "JCAP": {
        "host": "sftp.ioppublishing.org",
        "port": 22,
        "username": "partner-sissa",
        "private_key_path": "",
        "password": "",
        "host_key": "ssh-rsa AAAAB3NzaC1yc2E...",  # full key in wjs/defaults/settings.py
        "atomic_rename": False,
        "remote_paths": {
            "accepted-articles": "partner-sissa/jcap/accepted-articles",
            "final-files": "partner-sissa/jcap/final-files",
        },
    },
}
```

**Auth (revised)**: originally key-based only, matching an SFTP-only chroot'd user
account — the standard pattern for this kind of automated delivery. Password auth was
added alongside it for an endpoint that only offers a password (real IOP credentials, per
`wjs/specs#2972`'s discussion, may be either). `private_key_path` takes precedence when
both are configured; if neither is set, `_send_via_sftp` raises `SFTPSendError` before
attempting a connection, rather than letting paramiko fail with a less clear error.
`remote_paths` keys are the flow names used by the `flow` parameter below.

**Host key verification (added)**: the server's host key is always verified —
`RejectPolicy`, never `AutoAddPolicy`/`WarningPolicy` (no `StrictHostKeyChecking=no`
equivalent: this channel carries production files and possibly a password, so an
unverified host is a MITM risk). Where the trusted key comes from:

- `host_key` set (`"<key-type> <base64>"`, i.e. an `ssh-keyscan -t ed25519 <host>` line
  without the leading hostname, fingerprint verified out-of-band with the publisher):
  it is the **only** key trusted for that endpoint. It is added to the client's own host
  keys under `host` (or `[host]:port` when `port != 22`, matching OpenSSH/paramiko's
  lookup name), and the system known_hosts is **not** loaded — paramiko consults the
  system host keys *before* the client's own, so a stale system entry would otherwise
  silently override the pin. Paramiko also uses the pinned key's type as the preferred
  host-key algorithm during negotiation, so the server presents the matching key.
- `host_key` empty: fall back to the system known_hosts of the OS user running the
  process (the Django-Q worker / web server user — not necessarily whoever tested the
  connection by hand, which is the usual cause of "Server '...' not found in
  known_hosts").

A configured but unparseable `host_key` (missing key type, bad base64, unknown type,
type/data mismatch) raises `SFTPSendError` before any connection is attempted, rather
than falling back to known_hosts. Pinning in settings (instead of a per-deploy
known_hosts file) keeps trust next to the rest of the endpoint's config and independent
of which OS user runs the send.

### Transport helper (new `metadata_export/sftp.py`)

```python
class SFTPSendError(Exception):
    """Raised when delivering a file over SFTP fails for any reason."""


def _send_via_sftp(article: Article, zip_bytes: bytes, *, journal_code: str, flow: str) -> None:
    """Upload zip_bytes to the configured SFTP endpoint for (journal_code, flow).

    With atomic_rename (default), uploads to a temporary remote name and renames to its
    final name only after the full write succeeds, so a downstream ingestion process
    never sees a partial file; without it, writes the final name directly. A failed
    write/rename removes the uploaded file on a best-effort basis. Raises SFTPSendError (never paramiko's own exceptions) on any failure, wrapping the
    original as __cause__.
    """
```

Filename: `{ms_no}.zip`, matching IOP's own sample naming
(`JCAP_101P_0825.zip`) — `ms_no` comes from
`ArticleWorkflow.preprint_id` via the same `map_ms_no` mapper the XML already uses.
Temp name: `.{ms_no}.zip.part`, written first, then `SFTPClient.posix_rename`'d to the
final name.

**Upload strategy (revised: `atomic_rename`)**: temp-name-then-rename is the default
(`atomic_rename` absent or `True`), but it is **not usable against IOP**. First real test
against IOP's endpoint: upload succeeded, then `posix_rename` failed with
`Operation unsupported`. Findings:

- `posix_rename` is not a remote command (SFTP has no shell) but the OpenSSH vendor
  request `SSH_FXP_EXTENDED "posix-rename@openssh.com"`, which maps to POSIX `rename(2)`
  and atomically replaces an existing target. Servers advertise the extensions they
  support in their `SSH_FXP_VERSION` reply; paramiko ignores that list and just sends the
  request, so a non-OpenSSH server answers `SSH_FX_OP_UNSUPPORTED`.
- IOP's server is AWS Transfer Family (`remote software version AWS_SFTP_1.2`, S3-backed).
  It lacks the extension, and it also rejects the standard `SSH_FXP_RENAME` with
  `SSH_FX_PERMISSION_DENIED` (checked with the OpenSSH `sftp` client's `rename`) — so
  falling back to `SFTPClient.rename()` would not help either. `get`, `rm` and
  overwriting `put` are all allowed.
- On S3-backed Transfer Family, an object only becomes visible once its upload
  completes, and IOP confirmed that writing straight to the final name is fine and that
  interrupted uploads are not processed.

So `WJS_REVIEW_IOP_SFTP["JCAP"]` sets `atomic_rename: False`: `{ms_no}.zip` is written
directly, and a re-send simply overwrites it. In both modes, if the write (or the rename)
fails, `_send_via_sftp` tries to `remove()` the uploaded file (temp or final name) so no
partial/stray file is left on the server; a cleanup failure (e.g. the connection is
already gone) is logged and swallowed, never masking the original error, which is still
raised as `SFTPSendError`.

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

Only the final DB writes (message log + AC resolve) are wrapped by `run()` itself;
zip-building and the network call are not wrapped in a transaction *by `run()`*.

**Known limitation:** `run()` is invoked via the `system_verifies_production_requirements`
transition from callers that already hold their own `transaction.atomic()`
(`VerifyProductionRequirements.run()`, `ConfirmProductionReadiness.run()` -- the EO confirm view), so on those two
paths the send still happens inside an open transaction. Only the direct call from
`export_production_zip --send` is transaction-free. This is accepted: the send must stay
synchronous (the user who triggered the action gets the outcome immediately), and the cost
is a DB connection held for the transfer, bounded by the SFTP timeouts below. A rollback
after a successful send leaves the article `ACCEPTED`; the retry re-uploads the same
`{ms_no}.zip`. Removing the limitation would mean sending before the callers open their
transaction and feeding the result into the transition.

**Timeouts:** `_send_via_sftp` bounds the TCP connect, SSH banner, authentication and SFTP
channel I/O with `config["timeout"]` (default `DEFAULT_TIMEOUT` = 30 s); a timeout surfaces
as `SFTPSendError` like any other transport failure.

## Testing

- `sftp._send_via_sftp`: mock `paramiko.SSHClient`/`SFTPClient` (no real network in the
  suite). Cases: success (temp name written, then renamed); connection failure wrapped
  as `SFTPSendError`; write failure wrapped as `SFTPSendError`; unconfigured
  `(journal_code, flow)` raises `SFTPSendError` (or a clear config error) rather than a
  raw `KeyError`.
  Host keys: no `host_key` → system known_hosts loaded, `RejectPolicy` set; `host_key`
  set → only that key registered (under `host` for port 22, `[host]:port` otherwise),
  system known_hosts not loaded, `RejectPolicy` still set; invalid `host_key` →
  `SFTPSendError` before `SSHClient` is instantiated.
  Upload strategy: `atomic_rename=False` → final name opened directly, no rename; a write
  failure removes the uploaded file (temp name with `atomic_rename`, final name without);
  a rename failure removes the temp file; a failing cleanup does not replace the
  original error (`__cause__`).
- `send_zip_to_iop`: delegates to `_send_via_sftp` with `journal_code="JCAP",
  flow="accepted-articles"`.
- `SendProductionXMLToPublisher.run()`: success path resolves any existing
  `PRODUCTION_EXPORT_SEND_FAILED` AC and logs the message; `SFTPSendError` path creates
  the AC for the EO role, does **not** raise, and the transition still completes; a
  non-`SFTPSendError` exception from zip-building still propagates unchanged (existing
  behavior, unaffected by this change).

## Open questions

- ~~Real IOP host/credentials/key~~ — **resolved**: host, username and host key are in
  the default settings; the credentials are filled in by the instance override, no code
  change needed.
- `wjs-test`'s own SFTP server (`#3159`) is provisioned separately (ansible/ops); this
  branch only adds the settings shape it will populate.
