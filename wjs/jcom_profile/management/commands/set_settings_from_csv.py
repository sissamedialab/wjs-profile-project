import sys
from argparse import RawTextHelpFormatter

from django.core.management.base import BaseCommand
from journal.models import Journal
from submission.models import Licence, Section
from utils.setting_handler import get_setting, save_setting


class Command(BaseCommand):
    """A management command to set group of settings from a CSV file."""

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
        parser.add_argument("--journal", required=True)
        parser.add_argument("--csv", required=True)
        parser.add_argument("--group-name")

    def _read_csv_as_dict(self, csv_file):
        """Read CSV file as a dictionary."""
        import csv

        with open(csv_file) as file:
            reader = csv.DictReader(file, delimiter=",", quotechar='"')
            return list(reader)

    def _set_submission_configuration(
        self, journal: Journal, csv_rows: list[dict[str, str | bool]]
    ) -> list[dict[str, str | bool]]:
        """Set submission configuration settings from CSV rows."""
        for row in csv_rows:
            if row["field name"] == "default_license":
                licence, __ = Licence.objects.get_or_create(short_name=row["current value"], journal=journal)
                row["current value"] = licence
            if row["field name"] == "default_section":
                section, __ = Section.objects.get_or_create(name=row["current value"], journal=journal)
                row["current value"] = section
            if row["field name"] == "default_language":
                row["current value"] = row["current value"].lower()
            if row["current value"] == "false":
                row["current value"] = False
            if row["current value"] == "true":
                row["current value"] = True
            setattr(journal.submissionconfiguration, row["field name"], row["current value"])
        journal.submissionconfiguration.save()
        return csv_rows

    def _set_settings(self, journal: Journal, csv_rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Set settings from CSV rows."""
        applied_settings = []
        for row in csv_rows:
            if row["value"] and not row["value"].startswith("NOT USED"):
                save_setting(
                    setting_group_name=row["group"],
                    setting_name=row["name"],
                    journal=journal,
                    value=row["value"],
                )
                value = get_setting(
                    journal=journal, setting_group_name=row["group"], setting_name=row["name"]
                ).processed_value
                applied_settings.append({"name": f'{row["group"]}|{row["name"]}', "value": value})
        return applied_settings

    def handle(self, *args, **options):
        """Assign a setting."""
        csv_rows = self._read_csv_as_dict(options["csv"])
        journal = Journal.objects.get(code=options["journal"])
        if options["group_name"] == "submissionconfiguration":
            applied_settings = self._set_submission_configuration(journal, csv_rows)
        else:
            applied_settings = self._set_settings(journal, csv_rows)
        if options["verbosity"] > 1:
            settings = "\n - ".join([f'{setting["name"]}: {setting["value"]}' for setting in applied_settings])
            sys.stdout.write(f"Settings updated\n=========\n{settings}")
