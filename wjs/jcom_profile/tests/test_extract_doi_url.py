"""Test the command that extracs doi url from the journals."""
import csv

import pytest
from django.core import management
from django.utils import timezone
from identifiers.models import Identifier
from submission import models as submission_models
from utils.setting_handler import save_setting


@pytest.mark.django_db
def test_extract_doi_url(
    tmp_path,
    account_factory,
    article_factory,
    journal_factory,
):
    """create jcom journal with one article and jcomal without articles to verify
    the data extraction. Output files in tmp_path
    """
    # The management command looks for journals with JCOM and JCOMAL codes
    prefix = "PREFIX"
    jcom = journal_factory("JCOM")
    save_setting("Identifiers", "crossref_prefix", jcom, prefix)
    jcomal = journal_factory("JCOMAL")
    save_setting("Identifiers", "crossref_prefix", jcomal, prefix)

    # Setup first journal with one published article
    correspondence_author = account_factory()
    correspondence_author.save()
    article = article_factory(
        journal=jcom,
        date_published=timezone.now(),
        stage=submission_models.STAGE_PUBLISHED,
        correspondence_author=correspondence_author,
    )
    article.authors.add(correspondence_author)
    article.snapshot_authors()
    Identifier.objects.create(
        identifier="111",
        article=article,
        id_type="doi",
    )
    # If a "pubid" identifier is present, the article.url property gives
    # .../article/pubid/JCOM_VVII_YYYY_SNN/
    # Else, it no suche identifier exists, we would have
    # .../article/id/334/
    Identifier.objects.create(
        identifier="JCOM_VVII_YYYY_SNN",
        article=article,
        id_type="pubid",
    )
    article.save()

    # Setup second journal with one published article
    article_two = article_factory(
        journal=jcomal,
        date_published=timezone.now(),
        stage=submission_models.STAGE_PUBLISHED,
        correspondence_author=correspondence_author,
    )
    article_two.authors.add(correspondence_author)
    article_two.snapshot_authors()
    Identifier.objects.create(
        identifier="222",
        article=article_two,
        id_type="doi",
    )
    article_two.save()

    # call with optional argument store dir
    store_dir_arg = f"--store-dir={tmp_path}"
    my_email = "my@email.com"
    management.call_command("extract_doi_url", store_dir_arg, my_email)

    # Check file content for first journal
    with open(tmp_path / f"{jcom.code}_doi_url.txt") as csv_file:
        csv_reader = csv.reader(csv_file, delimiter="\t")
        # expect the csv file to have two lines
        rows = list(csv_reader)
        assert len(rows) == 2

        # first line is for colum headers
        header_row = rows[0]
        assert len(header_row) == 1
        assert header_row[0] == f"H: email={my_email};fromPrefix={prefix}"

        # second line contains our article's info
        data_row = rows[1]
        assert data_row[0] == article.get_identifier("doi")
        assert data_row[1] == article.url
        assert "article/pubid/JCOM" in data_row[1]

    # Check file content for second journal
    with open(tmp_path / f"{jcomal.code}_doi_url.txt") as csv_file_two:
        csv_reader = csv.reader(csv_file_two, delimiter="\t")
        # expect the csv file to have two lines
        rows = list(csv_reader)
        assert len(rows) == 2

        # first line is for colum headers
        header_row = rows[0]
        assert len(header_row) == 1
        assert header_row[0] == f"H: email={my_email};fromPrefix={prefix}"

        # second line contains our article's info
        data_row = rows[1]
        assert data_row[0] == article_two.get_identifier("doi")
        assert data_row[1] == article_two.url
        assert "article/pubid/JCOMAL" not in data_row[1]

    assert True
