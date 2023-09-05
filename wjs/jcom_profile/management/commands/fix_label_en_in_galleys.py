"""Fix label language en in old JCOM galleys.
"""
import csv
import os

from django.core.management.base import BaseCommand
from journal.models import Journal
from submission.models import STAGE_PUBLISHED, Article
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Fix label language en in old galleys"  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        self.options = options
        store_dir = options["store_dir"]
        if not os.path.isdir(store_dir):
            logger.critical(f"No such directory {store_dir}")
            raise FileNotFoundError(f"No such directory {store_dir}")
        dir_path = os.path.join(str(store_dir), "")

        # JCOM
        # have to be fixed EPUB and PDF (not HTML) galley without language
        # but only if there are in the article  EPUB and PDF galley with the language ex: (es)
        # example galley with lang format: "EPUB (es)"" "PDF (es)"

        # JCOMAL (not to be fixed)
        # Note: issue 230
        # JCOMAL
        # 1222, 2516, PDF es
        # "1222", "2517", EPUB"
        # "1222", "2518", HTML
        # this paper has EPUB without lang but EPUB is in spanish not english
        # JCOMAL has a different logic in the languages of the galley
        journal = Journal.objects.get(code="JCOM")

        self.fix_galley_label_lang(dir_path, journal, check=options["check"])

    def add_arguments(self, parser):
        """Add arguments to command."""
        behavior = parser.add_mutually_exclusive_group(required=True)
        behavior.add_argument(
            "--check",
            action="store_true",
            help="Just report the situation: do not set anything.",
        )
        behavior.add_argument(
            "--force",
            action="store_true",
            help="Apply the corrections",
        )
        parser.add_argument(
            "--store-dir",
            default="/tmp",
            help="Where to store output log files. Defaults to %(default)s",
        )

    def fix_galley_label_lang(self, dir_path, journal, check=True):
        """Fix lang label en in old galleys."""
        file_name = journal.code + "_" + "articles_with_galleys_labels_to_fix.csv"
        with open(dir_path + file_name, "w", newline="") as doi_url_csvfile:
            writer = csv.writer(doi_url_csvfile, delimiter=";", quoting=csv.QUOTE_ALL)
            writer.writerow(["article_id", "pubid", "galley_id", "label", "correction"])
            pub_articles = Article.objects.filter(journal=journal, stage=STAGE_PUBLISHED).order_by("id")
            for article in pub_articles:
                all_article_galleys = article.galley_set.all()
                num_with_lang = 0
                num_no_lang = 0
                for a in all_article_galleys:
                    if ("PDF (" in a.label) or ("EPUB (" in a.label):
                        num_with_lang += 1
                    if ("PDF" == a.label) or ("EPUB" == a.label):
                        num_no_lang += 1
                # if there are PDF or EPUB galleys with lang and also without lang, add lang only where is missing
                if num_with_lang > 0 and num_no_lang > 0:
                    for g in all_article_galleys:
                        before_correction = g.label
                        correction = ""
                        # HTML galley have not to be modified
                        if ("HTML" not in g.label) and ("(" not in g.label):
                            correction = g.label + " (en)"
                            if not check:
                                g.label = correction
                                g.save()
                        # write csv file with all the articles to fix and marking the galley to be corrected
                        writer.writerow(
                            [
                                g.article_id,
                                article.get_identifier("pubid"),
                                g.id,
                                before_correction,
                                correction,
                            ],
                        )
