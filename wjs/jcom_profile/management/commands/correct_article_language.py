"""Una-tantum bug fix on article language.

During import from Drupal and wjapp, the article's language name was
set as "full-name", not as language 3-letter code. This script rectify
this.

"""
from django.core.management.base import BaseCommand
from journal.models import Journal
from submission.models import LANGUAGE_CHOICES, Article
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Correct articles language to code."  # noqa

    def handle(self, *args, **options):
        """Command entry point."""
        self.journal = Journal.objects.get(code=options["journal_code"])
        self.languages_by_name = {lang_tuple[1]: lang_tuple[0] for lang_tuple in LANGUAGE_CHOICES}
        self.languages_by_code = {lang_tuple[0]: lang_tuple[1] for lang_tuple in LANGUAGE_CHOICES}
        self.process_journal(check=options["check"])

    def process_journal(self, check=True):
        """Check (and correct) all articles of a journal."""
        for article in Article.objects.filter(journal=self.journal):

            if article.language in self.languages_by_name:
                lang_code = self.languages_by_name[article.language]
                if check:
                    logger.warning(f'Please correct "{article.language}" to "{lang_code}" for "{article.url}"')
                else:
                    logger.warning(f'Correcting "{article.language}" to "{lang_code}" for "{article.url}"')

                    article.language = self.languages_by_name[article.language]
                    article.save()

            else:
                if article.language in self.languages_by_code:
                    logger.debug(f'Good "{article.language}" for "{article.url}"')
                else:
                    logger.error(f'Unknown "{article.language}" for article "{article.url}"')
                    continue

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
            "--journal-code",
            choices=("JCOM", "JCOMAL"),
            default="JCOM",
            help="The code of the journal that we are working on. Defaults to %(default)s.",
        )
