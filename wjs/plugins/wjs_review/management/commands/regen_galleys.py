import argparse
import io
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from core import files
from django.core.files import File as DjangoFile
from django.core.management.base import BaseCommand, CommandError
from plugins.wjs_review.logic__production import JcomAssistantClient
from submission.models import Article

if TYPE_CHECKING:
    from core.models import File as JanewayFile


class Command(BaseCommand):
    help = """Regenerate galleys.
    This command works in three steps:
    1. get the sources for editing
       - call as `m regen_galleys 1234`
       - the zip sources are extracted into /tmp/JCOM_1234_sources (or see -o)

    2. generate new galleys from the edited sources
       - call as `m regen_galleys 1234 --test FOLDER
       - all the content of FOLDER is zipped and passed to JcomAssistant
       - the result is extracted into new folder FOLDER_galleys

    3. attach the new galleys to the article
       - call as `m regen_galleys 1234 --test FOLDER --attach
       - same as point 2., but the PDF, HTML and EPUB files are attached to the article
       - NOT IMPLEMENTED!
    """  # noqa: A003

    def add_arguments(self, parser):
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.add_argument("article_id", type=int, help="ID of the article")
        parser.add_argument("--test", type=str, help="Path to folder with edited sources for step 2/3")
        parser.add_argument("--attach", action="store_true", help="Attach generated galleys to article (step 3)")
        parser.add_argument("-o", "--output", type=str, help="Output directory for extracted sources (step 1)")

    def handle(self, *args, **options):
        article_id = options["article_id"]
        test_folder = options.get("test")
        attach = options.get("attach", False)
        output_dir = options.get("output")

        try:
            article = Article.objects.get(id=article_id)
        except Article.DoesNotExist:
            raise CommandError(f"Article with id {article_id} does not exist")

        if test_folder:
            # Step 2 or 3: Generate galleys from edited sources
            self._generate_galleys_from_folder(article, test_folder, attach=attach)
        else:
            # Step 1: Extract sources for editing
            self._extract_sources_for_editing(article, output_dir)

    def _extract_sources_for_editing(self, article: Article, output_dir: Path) -> None:
        """Step 1: Extract source files for editing."""  # noqa: DOC501
        galleys_source_file = article.articleworkflow.publication_galleys_source_file
        if not galleys_source_file:
            raise CommandError(f"No publication galleys source file found for article {article.id}")

        galleys_filename = galleys_source_file.uuid_filename
        galleys_path = Path(article.folder_path()) / galleys_filename
        if not galleys_path.exists():
            raise CommandError(f'Source file for {article.id} "{galleys_path}" does not exist!')

        extract_dir = Path(output_dir) if output_dir else Path(f"/tmp/JCOM_{article.id}_sources")  # noqa: S108
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(galleys_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
            self.stdout.write(self.style.SUCCESS(f"Sources extracted to: {extract_dir}"))
        except zipfile.BadZipFile:
            raise CommandError(f"Invalid zip file: {galleys_path}")

    def _generate_galleys_from_folder(
        self,
        article: Article,
        test_folder: Path,
        *,
        attach: bool,
    ):
        """Step 2/3: Generate galleys from edited sources."""  # noqa: DOC501
        test_path = Path(test_folder)
        if not test_path.exists():
            raise CommandError(f"Test folder does not exist: {test_folder}")

        # Create a temporary zip file with the folder contents
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_zip:
            temp_zip_path = Path(temp_zip.name)

        try:
            # Create zip from folder contents
            with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for file_path in test_path.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(test_path)
                        zipf.write(file_path, arcname)

            with mock.patch("plugins.wjs_review.communication_utils.notify_async_event"):
                ja_client = JcomAssistantClient(
                    archive_with_files_to_process=temp_zip_path,
                    user=None,
                )
                response = ja_client.ask_jcomassistant_to_process()
                archive_with_galleys = response.content

            # Extract galleys to output folder
            output_folder = test_path.parent / f"{test_path.name}_galleys"
            output_folder.mkdir(exist_ok=True)

            # Use in-memory BytesIO object instead of writing to disk
            with zipfile.ZipFile(io.BytesIO(archive_with_galleys), "r") as zip_ref:
                zip_ref.extractall(output_folder)

            self.stdout.write(self.style.SUCCESS(f"Galleys generated in: {output_folder}"))

            if attach:
                # Step 3: Attach galleys to article
                self._attach_galleys_to_article(article, output_folder)

        finally:
            # Clean up temporary zip file
            temp_zip_path.unlink(missing_ok=True)

    def _attach_galleys_to_article(self, article: Article, galleys_folder: str):
        """Step 3: Attach generated galleys to the article."""
        galleys_path = Path(galleys_folder)

        def replace_helper(article: Article, label: str, new_file: Path):
            file_to_replace: JanewayFile = article.galley_set.get(label=label).file
            files.overwrite_file(
                uploaded_file=DjangoFile(new_file.open("rb"), name=new_file.name),
                file_to_replace=file_to_replace,
                path_parts=("articles", article.pk),
            )
            self.stdout.write(f"Replaced {label}: {new_file.name}")

        pdf_files = list(galleys_path.glob("*.pdf"))
        if pdf_files:
            replace_helper(article, "PDF", pdf_files[0])

        epub_files = list(galleys_path.glob("*.epub"))
        if epub_files:
            replace_helper(article, "EPUB", epub_files[0])

        html_files = list(galleys_path.glob("*.html"))
        if html_files:
            self.stdout.write("Skipping HTML!")

        self.stdout.write(self.style.SUCCESS(f"Galleys attached to article {article.id}"))
