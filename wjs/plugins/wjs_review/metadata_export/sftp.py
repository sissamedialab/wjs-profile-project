"""SFTP transport for delivering the production export zip to a publisher.

Settings-driven, per ``(journal_code, flow)`` (``settings.WJS_REVIEW_IOP_SFTP``, see
``wjs/defaults/settings.py``). Key-based auth (``private_key_path``) takes precedence when
configured; password auth (``password``) is the fallback for an endpoint that only offers
it. The server's host key is always verified (``RejectPolicy``, never ``AutoAddPolicy``): an
unrecognized host key is a hard failure, not a prompt to trust it. When ``host_key`` is
configured, it is the only key trusted for that endpoint (the system's known_hosts is not
consulted); otherwise the system known_hosts of the OS user running the process is used.

Upload strategy (``atomic_rename``, default ``True``): write to a temporary name, then
``posix_rename`` it to the final one, so the other end never sees a partial file. This needs
the OpenSSH ``posix-rename@openssh.com`` extension and rename permission; endpoints lacking
either (e.g. AWS Transfer Family, which forbids rename) set it to ``False`` and are written
directly under the final name -- acceptable only when the receiving side ignores interrupted
uploads. Either way, a failed upload is removed on a best-effort basis.

Every network step (TCP connect, SSH banner, authentication, SFTP reads/writes) is bounded by a
timeout (``timeout`` in the endpoint's config, default ``DEFAULT_TIMEOUT`` seconds): the send is
synchronous and may run while the caller holds a DB transaction, so a hung server must fail
fast as ``SFTPSendError`` rather than block indefinitely.
"""

from typing import Optional

import paramiko
from django.conf import settings
from submission.models import Article
from utils.logger import get_logger

from . import mappers

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30


class SFTPSendError(Exception):
    """Raised when delivering a file to a publisher over SFTP fails for any reason."""


def _pinned_host_key(config: dict, *, journal_code: str) -> Optional[paramiko.hostkeys.HostKeyEntry]:
    """Parse the endpoint's pinned ``host_key`` (``"<key-type> <base64>"``), if configured.

    Returns ``None`` when no ``host_key`` is configured. Raises ``SFTPSendError`` when it is
    configured but unparseable, so a typo fails before any connection is attempted rather
    than silently falling back to the system known_hosts.
    """
    host_key = config.get("host_key")
    if not host_key:
        return None
    port = config.get("port", 22)
    host_entry = config["host"] if port == 22 else f"[{config['host']}]:{port}"
    try:
        entry = paramiko.hostkeys.HostKeyEntry.from_line(f"{host_entry} {host_key.strip()}")
    except (paramiko.hostkeys.InvalidHostKey, paramiko.SSHException) as exc:
        raise SFTPSendError(f"invalid SFTP host_key configured for journal {journal_code!r}: {exc}") from exc
    if entry is None:
        raise SFTPSendError(
            f"invalid SFTP host_key configured for journal {journal_code!r} (expected '<key-type> <base64-key>')"
        )
    return entry


def _remove_quietly(sftp_client: paramiko.SFTPClient, path: str) -> None:
    """Try to remove a partially uploaded remote file, never raising.

    Called while an upload error is already propagating: a cleanup failure (e.g. the
    connection is gone) must not mask the original error.
    """
    try:
        sftp_client.remove(path)
    except Exception as exc:
        logger.warning("could not remove partial SFTP upload %r: %s", path, exc)


def _send_via_sftp(article: Article, zip_bytes: bytes, *, journal_code: str, flow: str) -> None:
    """Upload ``zip_bytes`` to the configured SFTP endpoint for ``(journal_code, flow)``.

    With ``atomic_rename`` (the default), uploads to a temporary remote name
    (``.{ms_no}.zip.part``) and renames it to its final name (``{ms_no}.zip``) only after the
    full write succeeds, so a downstream ingestion process on the other end never sees a
    partial file; without it, writes ``{ms_no}.zip`` directly (overwriting any previous
    upload). On a failed write or rename, the uploaded file is removed if possible. Raises ``SFTPSendError`` -- never a
    raw ``paramiko``/``OSError`` exception -- on any failure, with the original exception as
    ``__cause__``.
    """
    config = settings.WJS_REVIEW_IOP_SFTP.get(journal_code)
    if config is None:
        raise SFTPSendError(f"no SFTP configuration for journal {journal_code!r}")

    remote_dir = config["remote_paths"].get(flow)
    if remote_dir is None:
        raise SFTPSendError(f"no SFTP remote path configured for flow {flow!r} (journal {journal_code!r})")

    ms_no = mappers.map_ms_no(article)
    if not ms_no:
        raise SFTPSendError(
            f"article {article.pk} has no ms_no (ArticleWorkflow.preprint_id); cannot name the remote file",
        )

    final_name = f"{ms_no}.zip"
    temp_path = f"{remote_dir}/.{final_name}.part"
    final_path = f"{remote_dir}/{final_name}"
    atomic_rename = config.get("atomic_rename", True)
    timeout = config.get("timeout", DEFAULT_TIMEOUT)
    upload_path = temp_path if atomic_rename else final_path

    if config.get("private_key_path"):
        auth_kwargs = {"key_filename": config["private_key_path"]}
    elif config.get("password"):
        auth_kwargs = {"password": config["password"]}
    else:
        raise SFTPSendError(
            f"no SFTP credentials configured for journal {journal_code!r} (need private_key_path or password)"
        )

    pinned_host_key = _pinned_host_key(config, journal_code=journal_code)

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        if pinned_host_key is not None:
            # Do not also load the system known_hosts: paramiko checks it *before* the
            # client's own host keys, so a stale system entry would override the pin.
            for hostname in pinned_host_key.hostnames:
                client.get_host_keys().add(hostname, pinned_host_key.key.get_name(), pinned_host_key.key)
        else:
            client.load_system_host_keys()
        try:
            client.connect(
                hostname=config["host"],
                port=config.get("port", 22),
                username=config["username"],
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                **auth_kwargs,
            )
            sftp_client = client.open_sftp()
            # connect()'s timeouts do not cover the SFTP session itself: bound reads/writes too.
            sftp_client.get_channel().settimeout(timeout)
            try:
                with sftp_client.open(upload_path, "wb") as remote_file:
                    remote_file.write(zip_bytes)
                if atomic_rename:
                    sftp_client.posix_rename(temp_path, final_path)
            except Exception:
                _remove_quietly(sftp_client, upload_path)
                raise
        finally:
            client.close()
    except Exception as exc:
        raise SFTPSendError(
            f"failed to send {final_name!r} to {journal_code}/{flow} via SFTP: {exc}",
        ) from exc
