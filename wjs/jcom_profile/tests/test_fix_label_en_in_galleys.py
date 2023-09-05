"""Test the command that fix language label in jcom galley where missing."""
import csv

import pytest
from core import models as core_models
from django.core import management
from django.utils import timezone
from identifiers.models import Identifier
from submission import models as submission_models


@pytest.mark.fix_labels
class TestFixLabelsGalley:
    """test related to fix of labels in galleys"""

    def get_report_rows(
        self,
        path,
        journal,
    ):
        """read management command report"""
        out = []
        with open(path / f"{journal.code}_articles_with_galleys_labels_to_fix.csv") as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=";")
            out = list(csv_reader)
        return out

    @pytest.mark.django_db
    def test_two_labels_one_corrected(
        self,
        tmp_path,
        account_factory,
        article_factory,
        journal_factory,
    ):
        """create jcom journal, one article with one galley to fix and one galley not to fix.
        Output files in tmp_path
        """

        jcom = journal_factory("JCOM")

        # corr author
        correspondence_author = account_factory()
        correspondence_author.save()

        # article with 2 galley with labels:
        # "PDF"      (to be corrected -> "PDF (en)")
        # "PDF (es)" (must not be corrected)
        article = article_factory(
            journal=jcom,
            date_published=timezone.now(),
            stage=submission_models.STAGE_PUBLISHED,
            correspondence_author=correspondence_author,
        )
        article.authors.add(correspondence_author)
        article.snapshot_authors()
        # pubid identifier used in the report
        # If a "pubid" identifier is present, the article.url property gives
        # .../article/pubid/JCOM_VVII_YYYY_SNN/
        # Else, it no such identifier exists, we would have
        # .../article/id/334/
        Identifier.objects.create(
            identifier="JCOM_VVII_YYYY_SNN",
            article=article,
            id_type="pubid",
        )
        article.save()

        # first galley to fix
        galley_file_1 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_1_to_fix = core_models.Galley.objects.create(
            article=article,
            file=galley_file_1,
            label="PDF",
            type="pdf",
        )

        # second galley not to fix
        galley_file_2 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_2_not_to_fix = core_models.Galley.objects.create(
            article=article,
            file=galley_file_2,
            label="PDF (es)",
            type="pdf",
        )

        # call management command with optional argument store dir and force
        store_dir_arg = f"--store-dir={tmp_path}"
        management.call_command("fix_label_en_in_galleys", store_dir_arg, "--force")

        # Check file content for journal jcom (only 1 article must be present)

        # expect csv file with 1 row headers and two rows content
        rows = self.get_report_rows(tmp_path, jcom)
        assert len(rows) == 3

        # first row is for colum headers
        header_row = rows[0]
        assert len(header_row) == 5
        assert ";".join(f'"{h}"' for h in header_row) == '"article_id";"pubid";"galley_id";"label";"correction"'

        # second and third row contains article data
        for i in (1, 2):
            data_row = rows[i]
            assert data_row[1] == article.get_identifier("pubid")
            if data_row[2] == new_galley_1_to_fix.id:
                # current
                assert data_row[3] == "PDF"
                # correction
                assert data_row[4] == "PDF (en)"
            if data_row[2] == new_galley_2_not_to_fix.id:
                # current
                assert data_row[3] == "PDF (es)"
                # not changed
                assert data_row[4] == ""

        # check labels correction refreshing from db
        new_galley_1_to_fix.refresh_from_db()
        # corrected
        assert new_galley_1_to_fix.label == "PDF (en)"

        new_galley_2_not_to_fix.refresh_from_db()
        # not changed
        assert new_galley_2_not_to_fix.label == "PDF (es)"

    @pytest.mark.django_db
    def test_one_label_no_correction(
        self,
        tmp_path,
        account_factory,
        article_factory,
        journal_factory,
    ):
        """create jcom journal with one article and one galley not to be changed.
        Output files in tmp_path
        """

        jcom = journal_factory("JCOM")

        # corr author
        correspondence_author = account_factory()
        correspondence_author.save()

        # article: 1 galley with label "PDF" (must not be corrected)
        article = article_factory(
            journal=jcom,
            date_published=timezone.now(),
            stage=submission_models.STAGE_PUBLISHED,
            correspondence_author=correspondence_author,
        )
        article.authors.add(correspondence_author)
        article.snapshot_authors()
        # pubid identifier used in the report
        # If a "pubid" identifier is present, the article.url property gives
        # .../article/pubid/JCOM_VVII_YYYY_SNN/
        # Else, it no such identifier exists, we would have
        # .../article/id/334/
        Identifier.objects.create(
            identifier="JCOM_VVII_YYYY_SZZ",
            article=article,
            id_type="pubid",
        )
        article.save()

        # one galley
        galley_file_1 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_1 = core_models.Galley.objects.create(
            article=article,
            file=galley_file_1,
            label="PDF",
            type="pdf",
        )

        # call management command with optional argument store dir and force
        store_dir_arg = f"--store-dir={tmp_path}"
        management.call_command("fix_label_en_in_galleys", store_dir_arg, "--force")

        # expect csv report with only headers
        rows = self.get_report_rows(tmp_path, jcom)
        assert len(rows) == 1

        # the only row is for colum headers
        header_row = rows[0]
        assert len(header_row) == 5
        assert ";".join(f'"{h}"' for h in header_row) == '"article_id";"pubid";"galley_id";"label";"correction"'

        # not changed
        new_galley_1.refresh_from_db()
        assert new_galley_1.label == "PDF"

    @pytest.mark.django_db
    def test_two_labels_no_correction_no_lang(
        self,
        tmp_path,
        account_factory,
        article_factory,
        journal_factory,
    ):
        """create jcom journal with one article with two galleys not to be changed.
        Output files in tmp_path
        """

        jcom = journal_factory("JCOM")

        # corr author
        correspondence_author = account_factory()
        correspondence_author.save()

        # article: 1 galley with label "PDF" one with label "EPUB" (must not be corrected)
        article = article_factory(
            journal=jcom,
            date_published=timezone.now(),
            stage=submission_models.STAGE_PUBLISHED,
            correspondence_author=correspondence_author,
        )
        article.authors.add(correspondence_author)
        article.snapshot_authors()
        # pubid identifier used in the report
        # If a "pubid" identifier is present, the article.url property gives
        # .../article/pubid/JCOM_VVII_YYYY_SNN/
        # Else, it no such identifier exists, we would have
        # .../article/id/334/
        Identifier.objects.create(
            identifier="JCOM_VVII_YYYY_SZZ",
            article=article,
            id_type="pubid",
        )
        article.save()

        # first galley
        galley_file_1 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_1 = core_models.Galley.objects.create(
            article=article,
            file=galley_file_1,
            label="PDF",
            type="pdf",
        )

        # second galley
        galley_file_2 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_2 = core_models.Galley.objects.create(
            article=article,
            file=galley_file_2,
            label="EPUB",
            type="other",
        )

        # call management command with optional argument store dir and force
        store_dir_arg = f"--store-dir={tmp_path}"
        management.call_command("fix_label_en_in_galleys", store_dir_arg, "--force")

        # expect csv report with only headers
        rows = self.get_report_rows(tmp_path, jcom)
        assert len(rows) == 1

        # the only row is for colum headers
        header_row = rows[0]
        assert len(header_row) == 5
        assert ";".join(f'"{h}"' for h in header_row) == '"article_id";"pubid";"galley_id";"label";"correction"'

        # not changed
        new_galley_1.refresh_from_db()
        assert new_galley_1.label == "PDF"

        # not changed
        new_galley_2.refresh_from_db()
        assert new_galley_2.label == "EPUB"

    @pytest.mark.django_db
    def test_two_labels_no_correction_with_lang(
        self,
        tmp_path,
        account_factory,
        article_factory,
        journal_factory,
    ):
        """create jcom journal with one article with two galleys not to be changed.
        Output files in tmp_path
        """

        jcom = journal_factory("JCOM")

        # corr author
        correspondence_author = account_factory()
        correspondence_author.save()

        # article: 1 galley with 2 labels "PDF (it)" "PDF (en)" (must not be corrected)
        article = article_factory(
            journal=jcom,
            date_published=timezone.now(),
            stage=submission_models.STAGE_PUBLISHED,
            correspondence_author=correspondence_author,
        )
        article.authors.add(correspondence_author)
        article.snapshot_authors()
        # pubid identifier used in the report
        # If a "pubid" identifier is present, the article.url property gives
        # .../article/pubid/JCOM_VVII_YYYY_SNN/
        # Else, it no such identifier exists, we would have
        # .../article/id/334/
        Identifier.objects.create(
            identifier="JCOM_VVII_YYYY_SZZ",
            article=article,
            id_type="pubid",
        )
        article.save()

        # first galley
        galley_file_1 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_1 = core_models.Galley.objects.create(
            article=article,
            file=galley_file_1,
            label="PDF (it)",
            type="pdf",
        )

        # second galley
        galley_file_2 = core_models.File.objects.create(
            article_id=article.id,
            is_galley=True,
        )

        new_galley_2 = core_models.Galley.objects.create(
            article=article,
            file=galley_file_2,
            label="PDF (en)",
            type="pdf",
        )

        # call management command with optional argument store dir and force
        store_dir_arg = f"--store-dir={tmp_path}"
        management.call_command("fix_label_en_in_galleys", store_dir_arg, "--force")

        # expect csv report with only headers
        rows = self.get_report_rows(tmp_path, jcom)
        assert len(rows) == 1

        # the only row is for colum headers
        header_row = rows[0]
        assert len(header_row) == 5
        assert ";".join(f'"{h}"' for h in header_row) == '"article_id";"pubid";"galley_id";"label";"correction"'

        # not changed
        new_galley_1.refresh_from_db()
        assert new_galley_1.label == "PDF (it)"

        # not changed
        new_galley_2.refresh_from_db()
        assert new_galley_2.label == "PDF (en)"
