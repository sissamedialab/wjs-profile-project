"""Tests for ``metadata_export.sftp`` (the SFTP transport behind ``publishers.send_zip_to_iop``).

No real network access: ``paramiko.SSHClient`` is mocked throughout.
"""

from unittest import mock

import paramiko
import pytest
from plugins.wjs_review.metadata_export import sftp

SFTP_TEST_SETTINGS = {
    "JCAP": {
        "host": "sftp.example.test",
        "port": 22,
        "username": "sissa",
        "private_key_path": "/keys/id_ed25519",
        "password": "",
        "host_key": "",
        "remote_paths": {
            "accepted-articles": "partner-sissa/jcap/accepted-articles",
            "final-files": "partner-sissa/jcap/final-files",
        },
    },
}

# Throwaway public key, generated only for these tests.
PINNED_HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDGfZ/Sq668R/K8y65fk/qF2uw0ORCG2cuLNJwXsRB8A"


@pytest.mark.django_db
def test_send_via_sftp_uploads_to_temp_name_then_renames_to_final_name(article, settings):
    """A successful upload writes to a temp remote name, then renames it to ``{ms_no}.zip``."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS
    ms_no = article.articleworkflow.preprint_id
    zip_bytes = b"pretend-zip-bytes"

    mock_sftp_client = mock.MagicMock()
    mock_ssh_client = mock.MagicMock()
    mock_ssh_client.open_sftp.return_value = mock_sftp_client

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        sftp._send_via_sftp(article, zip_bytes, journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client.connect.assert_called_once_with(
        hostname="sftp.example.test",
        port=22,
        username="sissa",
        timeout=sftp.DEFAULT_TIMEOUT,
        banner_timeout=sftp.DEFAULT_TIMEOUT,
        auth_timeout=sftp.DEFAULT_TIMEOUT,
        key_filename="/keys/id_ed25519",
    )
    remote_dir = "partner-sissa/jcap/accepted-articles"
    temp_path = f"{remote_dir}/.{ms_no}.zip.part"
    final_path = f"{remote_dir}/{ms_no}.zip"
    mock_sftp_client.open.assert_called_once_with(temp_path, "wb")
    written_handle = mock_sftp_client.open.return_value.__enter__.return_value
    written_handle.write.assert_called_once_with(zip_bytes)
    mock_sftp_client.posix_rename.assert_called_once_with(temp_path, final_path)
    mock_ssh_client.close.assert_called_once()


@pytest.mark.django_db
def test_send_via_sftp_uses_password_when_no_private_key_path(article, settings):
    """No private_key_path configured: connect() authenticates with the configured password instead."""
    settings.WJS_REVIEW_IOP_SFTP = {
        "JCAP": {
            **SFTP_TEST_SETTINGS["JCAP"],
            "private_key_path": "",
            "password": "s3cr3t",  # noqa: S105 -- test fixture value, not a real credential
        },
    }

    mock_sftp_client = mock.MagicMock()
    mock_ssh_client = mock.MagicMock()
    mock_ssh_client.open_sftp.return_value = mock_sftp_client

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client.connect.assert_called_once_with(
        hostname="sftp.example.test",
        port=22,
        username="sissa",
        timeout=sftp.DEFAULT_TIMEOUT,
        banner_timeout=sftp.DEFAULT_TIMEOUT,
        auth_timeout=sftp.DEFAULT_TIMEOUT,
        password="s3cr3t",  # noqa: S106 -- test fixture value, not a real credential
    )


@pytest.mark.django_db
def test_send_via_sftp_raises_when_no_credentials_configured(article, settings):
    """Neither private_key_path nor password configured: fails fast, before opening any connection."""
    settings.WJS_REVIEW_IOP_SFTP = {
        "JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "private_key_path": "", "password": ""},
    }

    with mock.patch("plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient") as mock_ssh_client_class:
        with pytest.raises(sftp.SFTPSendError, match="no SFTP credentials configured"):
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client_class.assert_not_called()


@pytest.mark.django_db
def test_send_via_sftp_wraps_connection_failure(article, settings):
    """A paramiko connection failure is wrapped as SFTPSendError, not left to propagate raw."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS

    mock_ssh_client = mock.MagicMock()
    mock_ssh_client.connect.side_effect = paramiko.SSHException("connection refused")

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        with pytest.raises(sftp.SFTPSendError) as exc_info:
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    assert isinstance(exc_info.value.__cause__, paramiko.SSHException)
    mock_ssh_client.close.assert_called_once()


@pytest.mark.django_db
def test_send_via_sftp_raises_for_unconfigured_journal(article, settings):
    """A journal with no entry in WJS_REVIEW_IOP_SFTP raises SFTPSendError, not a raw KeyError."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS

    with pytest.raises(sftp.SFTPSendError, match="JINST"):
        sftp._send_via_sftp(article, b"zip-bytes", journal_code="JINST", flow="accepted-articles")


@pytest.mark.django_db
def test_send_via_sftp_raises_for_unconfigured_flow(article, settings):
    """A flow with no entry in the journal's remote_paths raises SFTPSendError, not a raw KeyError."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS

    with pytest.raises(sftp.SFTPSendError, match="unknown-flow"):
        sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="unknown-flow")


@pytest.mark.django_db
def test_send_via_sftp_raises_for_article_without_ms_no(article, settings):
    """An article with no ArticleWorkflow row (empty ms_no) raises SFTPSendError before opening any connection."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS
    article.articleworkflow.delete()
    article.refresh_from_db()

    with mock.patch("plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient") as mock_ssh_client_class:
        with pytest.raises(sftp.SFTPSendError, match="ms_no"):
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client_class.assert_not_called()


@pytest.mark.django_db
def test_send_via_sftp_uses_system_known_hosts_when_no_host_key(article, settings):
    """No host_key configured: the system known_hosts is loaded and unknown hosts are rejected."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS

    mock_ssh_client = mock.MagicMock()
    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client.load_system_host_keys.assert_called_once_with()
    mock_ssh_client.get_host_keys.return_value.add.assert_not_called()
    (policy,), _ = mock_ssh_client.set_missing_host_key_policy.call_args
    assert isinstance(policy, paramiko.RejectPolicy), f"expected RejectPolicy, got {policy!r}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "port,expected_hostname",
    [
        (22, "sftp.example.test"),
        (2222, "[sftp.example.test]:2222"),
    ],
)
def test_send_via_sftp_trusts_only_the_pinned_host_key(article, settings, port, expected_hostname):
    """A configured host_key is the only trusted key: added to the client, system known_hosts not loaded."""
    settings.WJS_REVIEW_IOP_SFTP = {
        "JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "port": port, "host_key": PINNED_HOST_KEY},
    }

    mock_ssh_client = mock.MagicMock()
    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client.load_system_host_keys.assert_not_called()
    add = mock_ssh_client.get_host_keys.return_value.add
    add.assert_called_once()
    hostname, key_type, key = add.call_args.args
    assert hostname == expected_hostname, f"host key registered under {hostname!r}, expected {expected_hostname!r}"
    assert key_type == "ssh-ed25519", f"unexpected key type {key_type!r}"
    assert f"{key.get_name()} {key.get_base64()}" == PINNED_HOST_KEY, "registered key differs from the pinned one"
    (policy,), _ = mock_ssh_client.set_missing_host_key_policy.call_args
    assert isinstance(policy, paramiko.RejectPolicy), f"expected RejectPolicy, got {policy!r}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "host_key",
    [
        "AAAAC3NzaC1lZDI1NTE5AAAAIDGfZ",  # key type missing
        "ssh-ed25519 not-base64!!!",
        "ssh-unknown AAAAC3NzaC1lZDI1NTE5AAAAIDGfZ/Sq668R/K8y65fk/qF2uw0ORCG2cuLNJwXsRB8A",
        "ssh-rsa AAAAC3NzaC1lZDI1NTE5AAAAIDGfZ/Sq668R/K8y65fk/qF2uw0ORCG2cuLNJwXsRB8A",  # type/data mismatch
    ],
)
def test_send_via_sftp_raises_for_invalid_host_key(article, settings, host_key):
    """An unparseable host_key fails before any connection, instead of falling back to known_hosts."""
    settings.WJS_REVIEW_IOP_SFTP = {
        "JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "host_key": host_key},
    }

    with mock.patch("plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient") as mock_ssh_client_class:
        with pytest.raises(sftp.SFTPSendError, match="invalid SFTP host_key"):
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    mock_ssh_client_class.assert_not_called()


def _mock_ssh_client_with_sftp():
    """Return ``(ssh_client, sftp_client)`` mocks wired together via ``open_sftp()``."""
    mock_sftp_client = mock.MagicMock()
    mock_ssh_client = mock.MagicMock()
    mock_ssh_client.open_sftp.return_value = mock_sftp_client
    return mock_ssh_client, mock_sftp_client


@pytest.mark.django_db
def test_send_via_sftp_applies_configured_timeout_to_connect_and_sftp_channel(article, settings):
    """A configured ``timeout`` bounds connect/banner/auth and the SFTP channel I/O."""
    settings.WJS_REVIEW_IOP_SFTP = {"JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "timeout": 7}}
    mock_ssh_client, mock_sftp_client = _mock_ssh_client_with_sftp()

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    connect_kwargs = mock_ssh_client.connect.call_args.kwargs
    assert (connect_kwargs["timeout"], connect_kwargs["banner_timeout"], connect_kwargs["auth_timeout"]) == (7, 7, 7)
    mock_sftp_client.get_channel.return_value.settimeout.assert_called_once_with(7)


@pytest.mark.django_db
def test_send_via_sftp_without_atomic_rename_writes_final_name_directly(article, settings):
    """atomic_rename=False: ``{ms_no}.zip`` is written directly and no rename is attempted."""
    settings.WJS_REVIEW_IOP_SFTP = {"JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "atomic_rename": False}}
    ms_no = article.articleworkflow.preprint_id
    zip_bytes = b"pretend-zip-bytes"
    mock_ssh_client, mock_sftp_client = _mock_ssh_client_with_sftp()

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        sftp._send_via_sftp(article, zip_bytes, journal_code="JCAP", flow="accepted-articles")

    final_path = f"partner-sissa/jcap/accepted-articles/{ms_no}.zip"
    mock_sftp_client.open.assert_called_once_with(final_path, "wb")
    written_handle = mock_sftp_client.open.return_value.__enter__.return_value
    written_handle.write.assert_called_once_with(zip_bytes)
    mock_sftp_client.posix_rename.assert_not_called()
    mock_sftp_client.rename.assert_not_called()
    mock_sftp_client.remove.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "atomic_rename,uploaded_name",
    [
        (True, ".{ms_no}.zip.part"),
        (False, "{ms_no}.zip"),
    ],
)
def test_send_via_sftp_removes_partial_upload_on_write_failure(article, settings, atomic_rename, uploaded_name):
    """A failed write removes the (partial) uploaded file and still raises SFTPSendError."""
    settings.WJS_REVIEW_IOP_SFTP = {"JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "atomic_rename": atomic_rename}}
    ms_no = article.articleworkflow.preprint_id
    mock_ssh_client, mock_sftp_client = _mock_ssh_client_with_sftp()
    written_handle = mock_sftp_client.open.return_value.__enter__.return_value
    written_handle.write.side_effect = OSError("connection lost")

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        with pytest.raises(sftp.SFTPSendError, match="connection lost"):
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    expected_path = f"partner-sissa/jcap/accepted-articles/{uploaded_name.format(ms_no=ms_no)}"
    mock_sftp_client.remove.assert_called_once_with(expected_path)
    mock_sftp_client.posix_rename.assert_not_called()
    mock_ssh_client.close.assert_called_once()


@pytest.mark.django_db
def test_send_via_sftp_removes_temp_file_on_rename_failure(article, settings):
    """A rejected rename (e.g. unsupported ``posix-rename@openssh.com``) removes the temp file."""
    settings.WJS_REVIEW_IOP_SFTP = SFTP_TEST_SETTINGS
    ms_no = article.articleworkflow.preprint_id
    mock_ssh_client, mock_sftp_client = _mock_ssh_client_with_sftp()
    mock_sftp_client.posix_rename.side_effect = OSError("Operation unsupported")

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        with pytest.raises(sftp.SFTPSendError, match="Operation unsupported") as exc_info:
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    assert isinstance(exc_info.value.__cause__, OSError), "original error must be kept as __cause__"
    mock_sftp_client.remove.assert_called_once_with(f"partner-sissa/jcap/accepted-articles/.{ms_no}.zip.part")


@pytest.mark.django_db
def test_send_via_sftp_cleanup_failure_does_not_mask_original_error(article, settings):
    """If removing the partial upload fails too, the original upload error is the one reported."""
    settings.WJS_REVIEW_IOP_SFTP = {"JCAP": {**SFTP_TEST_SETTINGS["JCAP"], "atomic_rename": False}}
    mock_ssh_client, mock_sftp_client = _mock_ssh_client_with_sftp()
    written_handle = mock_sftp_client.open.return_value.__enter__.return_value
    written_handle.write.side_effect = OSError("connection lost")
    mock_sftp_client.remove.side_effect = OSError("socket closed")

    with mock.patch(
        "plugins.wjs_review.metadata_export.sftp.paramiko.SSHClient",
        return_value=mock_ssh_client,
    ):
        with pytest.raises(sftp.SFTPSendError, match="connection lost") as exc_info:
            sftp._send_via_sftp(article, b"zip-bytes", journal_code="JCAP", flow="accepted-articles")

    assert str(exc_info.value.__cause__) == "connection lost", "cleanup error must not replace the original one"
    mock_sftp_client.remove.assert_called_once()
