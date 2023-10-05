from django.core.management.base import BaseCommand
from journal.models import Journal

from wjs.jcom_profile.models import Recipient

# Define the graph title, labels, etc...
# graph_title - appears at the top of the graph.
# graph_vlabel - label on y axis.
# graph_category - Munin organises graphs by category, here we have defined
# a new category for our site.
# graph_info - on the graph detail page, this is the title.
CONFIG = """graph_title Newsletter Recipients
graph_vlabel recipients count
graph_category WJS
graph_info Count people receiving WJS Newsletter.
"""

# Add the fields that will be monitored.
# Here we have just one field "objects".
# The 2 attributes are used on the field description section of the
# graph detail page.
CONFIG += """JCOM_recipients.label JCOM anonymous
JCOM_recipients.info Number of JCOM users without account who receive the Newsletter.
JCOM_recipients_account.label JCOM with account
JCOM_recipients_account.info Number of JCOM users with account who receive the Newsletter.
JCOMAL_recipients.label JCOMAL anonymous
JCOMAL_recipients.info Number of JCOMAL users without account who receive the Newsletter.
JCOMAL_recipients_account.label JCOMAL with account
JCOMAL_recipients_account.info Number of JCOMAL users with account who receive the Newsletter.
"""


class Command(BaseCommand):
    help = "Count newsletter Recipients"  # noqa

    def handle(self, *args, **options):
        if options["run_type"] == "config":
            self.stdout.write(CONFIG)
        else:
            self.run()

    def add_arguments(self, parser):
        parser.add_argument(
            "run_type",
            nargs="?",
            default="",
            type=str,
            choices=["config", ""],
            help='Either "config" or nothing. See munin docs',
        )

    def run(self):
        journal1 = Journal.objects.get(code="JCOM")
        journal2 = Journal.objects.get(code="JCOMAL")
        self.stdout.write(
            f"{journal1.code}_recipients.value "
            f"{Recipient.objects.filter(journal=journal1, user__isnull=True).count()}",
        )
        self.stdout.write(
            f"{journal1.code}_recipients_account.value "
            f"{Recipient.objects.filter(journal=journal1, user__isnull=False).count()}",
        )
        self.stdout.write(
            f"{journal2.code}_recipients.value "
            f"{Recipient.objects.filter(journal=journal2, user__isnull=True).count()}",
        )
        self.stdout.write(
            f"{journal2.code}_recipients_accounts.value "
            f"{Recipient.objects.filter(journal=journal2, user__isnull=False).count()}",
        )
