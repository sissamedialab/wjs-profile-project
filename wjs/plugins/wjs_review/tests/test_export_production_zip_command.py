"""Tests for the ``export_production_zip`` management command."""

import zipfile
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from plugins.wjs_review.metadata_export import service
from plugins.wjs_review.metadata_export.mappers import (
    UnsupportedArticleStageForExportError,
)
from plugins.wjs_review.metadata_export.sftp import SFTPSendError
from submission.models import STAGE_ACCEPTED, Article


@pytest.mark.django_db
def test_export_production_zip_writes_zip_named_after_ms_no_in_output_dir(accepted_article, tmp_path):
    # The article fixture pre-populates manuscript_files with placeholder File rows that have no
    # real content on disk (see wjs.jcom_profile.tests.conftest); drop them so building the zip
    # doesn't try to read a nonexistent file.
    accepted_article.manuscript_files.clear()

    call_command("export_production_zip", accepted_article.pk, str(tmp_path))

    ms_no = accepted_article.articleworkflow.preprint_id
    output_path = tmp_path / f"{ms_no}.zip"
    assert output_path.exists(), "command must write {ms_no}.zip inside output_dir"
    with zipfile.ZipFile(output_path) as archive:
        assert service.metadata_xml_entry_name(ms_no) in archive.namelist()
        assert f"{ms_no}/pdf/" in archive.namelist()
        assert f"{ms_no}/doc/" in archive.namelist()


@pytest.mark.django_db
def test_export_production_zip_raises_for_missing_article(tmp_path):
    missing_id = (Article.objects.order_by("-pk").first().pk if Article.objects.exists() else 0) + 999999

    with pytest.raises(CommandError, match="does not exist"):
        call_command("export_production_zip", missing_id, str(tmp_path))

    assert not any(tmp_path.iterdir()), "no file must be written when the article lookup fails"


@pytest.mark.django_db
def test_export_production_zip_propagates_unsupported_stage_error(article, tmp_path):
    """The ``article`` fixture is unsubmitted; the command doesn't catch mapper errors."""
    with pytest.raises(UnsupportedArticleStageForExportError):
        call_command("export_production_zip", article.pk, str(tmp_path))


@pytest.mark.django_db
def test_export_production_zip_force_accepted_bypasses_stage_check(article, tmp_path):
    """--force-accepted overrides article.stage in memory, so every stage-gated mapper sees an accepted article."""
    # The article fixture pre-populates manuscript_files with placeholder File rows that have no
    # real content on disk (see wjs.jcom_profile.tests.conftest); drop them so building the zip
    # doesn't try to read a nonexistent file.
    article.manuscript_files.clear()

    call_command("export_production_zip", article.pk, str(tmp_path), "--force-accepted")

    ms_no = article.articleworkflow.preprint_id
    with zipfile.ZipFile(tmp_path / f"{ms_no}.zip") as archive:
        xml = archive.read(service.metadata_xml_entry_name(ms_no)).decode()
    assert 'decision_status="accept"' in xml

    article.refresh_from_db()
    assert article.stage != STAGE_ACCEPTED, "the bypass must never persist the stage override to the database"


@pytest.mark.django_db
def test_export_production_zip_send_calls_send_production_xml_to_publisher_silently(accepted_article):
    """--send triggers the real send path (silently) instead of building and saving a local file."""
    with mock.patch(
        "plugins.wjs_review.management.commands.export_production_zip.SendProductionXMLToPublisher",
    ) as mock_sender_class:
        call_command("export_production_zip", accepted_article.pk, "--send")

    mock_sender_class.assert_called_once_with(articleworkflow=accepted_article.articleworkflow)
    mock_sender_class.return_value.run.assert_called_once_with(silent=True)


@pytest.mark.django_db
def test_export_production_zip_send_raises_command_error_on_sftp_send_error(accepted_article):
    """A failed send (SFTPSendError from run(silent=True)) surfaces as a CommandError, not a silent no-op."""
    with mock.patch(
        "plugins.wjs_review.management.commands.export_production_zip.SendProductionXMLToPublisher",
    ) as mock_sender_class:
        mock_sender_class.return_value.run.side_effect = SFTPSendError("connection refused")
        with pytest.raises(CommandError, match="connection refused"):
            call_command("export_production_zip", accepted_article.pk, "--send")


@pytest.mark.django_db
def test_export_production_zip_send_and_output_dir_are_mutually_exclusive(accepted_article, tmp_path):
    with pytest.raises(CommandError, match="output_dir"):
        call_command("export_production_zip", accepted_article.pk, str(tmp_path), "--send")


@pytest.mark.django_db
def test_export_production_zip_requires_output_dir_without_send(accepted_article):
    with pytest.raises(CommandError, match="output_dir"):
        call_command("export_production_zip", accepted_article.pk)
