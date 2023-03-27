"""Una-tantum bug fix on recipients with no topic selected.

Users who have not selected any topics when subscribing to publication
alert should actually be associated to all topics. This is because
instructions on the web page were misleading, hence those who did not
select anything thought that this way they would receive alerts for
all topics this.

"""
from django.core.management.base import BaseCommand
from journal.models import Journal
from submission.models import Keyword
from utils.logger import get_logger

from ...models import Recipient

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Correct articles language to code."  # noqa

    def handle(self, *args, **options):
        """Command entry point."""
        self.journal = Journal.objects.get(code=options["journal-code"])
        self.process_recipients(force=options["force"])

    def process_recipients(self, force=False):
        """Check (and correct) all recipients of a journal."""
        for recipient in Recipient.objects.filter(journal=self.journal):
            if recipient.topics.count() == 0:
                if force:
                    logger.warning(f'Setting all topics to "{recipient.newsletter_destination_email}".')
                    # TODO: link kwds to journal and use journal's kwds only!!!
                    recipient.topics.set(Keyword.objects.all())
                else:
                    logger.warning(f'Recipient "{recipient.newsletter_destination_email}" has no topics selected.')

            else:
                logger.debug(
                    f'Recipient "{recipient.newsletter_destination_email}" '
                    f"already has {recipient.topics.count()} topics.",
                )

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--force",
            action="store_true",
            help="Apply the corrections. The default behaviour is just to report.",
        )
        parser.add_argument(
            "journal-code",
            choices=("JCOM", "JCOMAL"),
            help="The code of the journal that we are working on.",
        )
