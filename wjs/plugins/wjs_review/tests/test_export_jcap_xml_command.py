"""Tests for the ``export_jcap_xml`` management command."""

import io

import pytest
from django.core.management import CommandError, call_command
from plugins.wjs_review.metadata_export.mappers import (
    UnsupportedArticleStageForExportError,
)
from submission.models import Article


@pytest.mark.django_db
def test_export_jcap_xml_writes_xml_to_stdout(accepted_article):
    out = io.StringIO()

    call_command("export_jcap_xml", accepted_article.pk, stdout=out)

    output = out.getvalue()
    assert output.strip().startswith("<?xml"), "command must print the rendered XML"
    assert f'ms_no="{accepted_article.articleworkflow.preprint_id}"' in output


@pytest.mark.django_db
def test_export_jcap_xml_raises_for_missing_article():
    missing_id = (Article.objects.order_by("-pk").first().pk if Article.objects.exists() else 0) + 999999

    with pytest.raises(CommandError, match="does not exist"):
        call_command("export_jcap_xml", missing_id)


@pytest.mark.django_db
def test_export_jcap_xml_propagates_unsupported_stage_error(article):
    """The ``article`` fixture is unsubmitted; the command doesn't catch mapper errors."""
    out = io.StringIO()

    with pytest.raises(UnsupportedArticleStageForExportError):
        call_command("export_jcap_xml", article.pk, stdout=out)
