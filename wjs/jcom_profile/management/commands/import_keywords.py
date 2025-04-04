"""Ensure all JCOM keywords are imported from wjapp."""

import mariadb
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from journal.models import Journal
from submission.models import Keyword
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):  # noqa: D101
    help = "Connect to wjApp jcom database and read users data."  # noqa: A003

    def handle(self, *args, **options):
        """Entry point."""
        if options["journal"] != "JCOM":
            raise NotImplementedError("Sorry, only JCOM for now.")

        self.journal = Journal.objects.get(code=options["journal"])
        self.cursor = self._get_cursor_on_wjapp()

        self.import_keywords()

    def add_arguments(self, parser):  # noqa: ANN001, PLR6301
        """Add arguments to command."""
        parser.add_argument(
            "--journal",
            choices=["JCOM", "JCOMAL", "ALL"],
            help="Specify the journal. Choices: JCOM, JCOMAL, ALL",
            default="JCOM",
        )

    def _get_cursor_on_wjapp(self):
        setting = f"WJAPP_{self.journal.code.upper()}_IMPORT_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)
        if connection_parameters is None:
            msg = (
                f"Missing connection parameters for {self.journal.code}. "
                f'Please ensure "{setting}" exists in settings.'
                f"Cannot connect, quitting."
            )
            raise CommandError(msg)

        if not connection_parameters.get("user", ""):
            msg = (
                f'Empty connection parameters for "{setting}". Please ensure `user`, `host`, etc. are correct.'
                f"Cannot connect, quitting."
            )
            raise CommandError(msg)

        connection = mariadb.connect(**connection_parameters)
        return connection.cursor()

    def import_keywords(self):
        """
        Import all keywords from wjapp.

        Ensure that they exist in WJS and that they are linked to the journal.
        """
        # TODO: refactor with import_articles_from_wjapp.set_keywords()
        self.cursor.execute("SELECT keywordName FROM Keyword WHERE keywordAvailable=1 ORDER BY keywordName")
        wjapp_kwds = set()  # Used below in case of failed sanity check.
        for record in self.cursor.fetchall():
            kwd = record[0]
            kwd_word = kwd.strip()
            wjapp_kwds.add(kwd_word)
            # Useless now, but keeping for when we'll do JCOMAL import:
            # in wjapp-JCOMAL, the keyword string contains all three
            # languages separated by ";". The first is English.
            if self.journal.code.upper() == "JCOMAL":
                kwd_word = kwd_word.split(";")[0].strip()
            keyword, created = Keyword.objects.get_or_create(word=kwd_word)
            if created:
                logger.debug(f'Created keyword "{kwd_word}"')

            # Always link kwd to journal (remember that journals have a set of kwds!)
            #
            # Even if the kwd was not created, it is possible that we got a pre-existing kwd that was linked only to
            # another journal.
            #
            # P.S. `add` won't duplicate an existing relation
            # https://docs.djangoproject.com/en/3.2/ref/models/relations/
            self.journal.keywords.add(keyword)

        # Sanity check:
        wjapp_count = self.cursor.rowcount
        wjs_count = Keyword.objects.filter(journal=self.journal).count()
        if wjapp_count != wjs_count:
            logger.warning(f"Different number of kwds in wjapp ({wjapp_count}) and wjs ({wjs_count}). Please check!")

            if wjs_count > wjapp_count:
                for wjs_k in Keyword.objects.filter(journal=self.journal).all().values_list("word", flat=True):
                    if wjs_k not in wjapp_kwds:
                        logger.warning(f'    "{wjs_k}" not in wjapp')
            else:
                msg = "wjapp > wjs not implemented!"
                raise CommandError(msg)
