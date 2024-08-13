import csv

from django.core.management.base import BaseCommand
from journal.models import Journal

from wjs.jcom_profile.custom_settings_utils import export_reminders, export_to_csv


class Command(BaseCommand):
    help = "Export a list of settings to a csv file."

    def add_arguments(self, parser):
        parser.add_argument(
            "application",
            help='The application to which the settings belong ("jcom_profile", "wjs_review" or "reminders").',
        )
        parser.add_argument(
            "--settings_list_csv",
            help="The path to the csv file containing the settings list. To generate them, run the commands `create_custom_settings` and `setup_review_settings` on an instance with `DEBUG=True`",
        )
        parser.add_argument("journal", help="The journal code to which the settings belong.")

    def handle(self, *args, **options):
        """Export a list of settings to a csv file."""
        journal = Journal.objects.get(code=options["journal"])
        application = options["application"]
        if application == "reminders":
            export_reminders(journal=journal)
        else:
            settings_list_csv = options.get("settings_list_csv")
            if not settings_list_csv:
                self.stderr.write("Please provide the path to the csv file containing the settings list.")
            with open(settings_list_csv) as f:
                csv_reader = csv.DictReader(f)
                settings_list = list(csv_reader)
            export_to_csv(application=application, journal=journal, settings_list=settings_list)
