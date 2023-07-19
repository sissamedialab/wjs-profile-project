"""Data Extraction"""
import csv
import os

from django.core.management.base import BaseCommand
from journal.models import Journal
from submission.models import STAGE_PUBLISHED, Article
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Extract doi url"  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        self.options = options
        store_dir = options["store_dir"]
        if not os.path.isdir(store_dir):
            logger.critical(f"No such directory {store_dir}")
            raise FileNotFoundError(f"No such directory {store_dir}")
        dir_path = os.path.join(str(store_dir), "")
        for journal_code in ["JCOM", "JCOMAL"]:
            journal = Journal.objects.get(code=journal_code)
            self.extract_doi_url(dir_path, journal)

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--store-dir",
            default="/tmp",
            help="Where to store output csv files. Defaults to %(default)s",
        )

    def extract_doi_url(self, dir_path, journal):
        """extract doi url"""
        file_name = journal.code + "_" + "doi_url.csv"
        with open(dir_path + file_name, "w", newline="") as doi_url_csvfile:
            writer = csv.writer(doi_url_csvfile, delimiter=",", quotechar='"', quoting=csv.QUOTE_ALL)
            writer.writerow(["doi", "url"])
            query_set = Article.objects.filter(journal=journal, stage=STAGE_PUBLISHED).order_by("-date_published")
            for article in query_set:
                if doi := article.get_identifier("doi"):
                    writer.writerow([doi, article.url])
                else:
                    logger.warning(f"Article {article.id} has no DOI. Pubid is {article.get_identifier('pubid')}.")
