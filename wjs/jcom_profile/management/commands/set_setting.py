import sys
from argparse import RawTextHelpFormatter

from django.core.management.base import BaseCommand
from journal.models import Journal
from utils.setting_handler import get_setting, save_setting


class Command(BaseCommand):
    """A management command to set a single setting value for a journal."""

    help = """
    Set a single setting value for a journal.

    Some special group names to handle non-core janeway settings:

    - submissionconfiguration: Save SubmissionConfiguration setting value.
    - plugins: use 'plugin:<group_name>' as group_name.
    """  # noqa

    def create_parser(self, *args, **kwargs):
        """Override parser to format help text properly."""
        parser = super().create_parser(*args, **kwargs)
        parser.formatter_class = RawTextHelpFormatter
        return parser

    def add_arguments(self, parser):
        """Adds arguments to Django's management command-line parser."""
        parser.add_argument("--journal")
        parser.add_argument("--group-name")
        parser.add_argument("--setting-name")
        parser.add_argument("--setting-value")

    def handle(self, *args, **options):
        """Assign a setting."""
        journal = Journal.objects.get(code=options["journal"])
        if options["group_name"] == "submissionconfiguration":
            setattr(journal.submissionconfiguration, options["setting_name"], options["setting_value"])
            journal.submissionconfiguration.save()
            value = getattr(journal.submissionconfiguration, options["setting_name"])
        else:
            save_setting(
                setting_group_name=options["group_name"],
                setting_name=options["setting_name"],
                journal=journal,
                value=options["setting_value"],
            )
            value = get_setting(
                journal=journal, setting_group_name=options["group_name"], setting_name=options["setting_name"]
            )
        if options["verbosity"] > 1:
            sys.stdout.write(f"Setting updated: {options['group_name']} - {options['setting_name']}: {value}")
