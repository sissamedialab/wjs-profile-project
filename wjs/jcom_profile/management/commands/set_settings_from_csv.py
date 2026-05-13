import sys
from argparse import RawTextHelpFormatter
from typing import Any

from django.core.management.base import BaseCommand
from journal.models import Journal
from submission.models import Field, Licence, Section
from utils.setting_handler import get_setting, save_setting


class Command(BaseCommand):
    """A management command to set group of settings from a CSV file."""

    help = """
    Set a single setting value for a journal.

    Some special group names to handle non-core janeway settings:

    - submissionconfiguration: Save SubmissionConfiguration setting value.
    - submissionfields: Create additional submission fields (existing are dropped)
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

    def _parse_value(self, value: Any) -> Any:
        """
        Parses the provided value into a boolean equivalent if applicable.

        The method interprets specific string values as boolean flags, converting them
        to either True or False. It ensures that commonly used textual or numerical
        representations of boolean-like data are handled appropriately. The method
        returns the original value if no conversion is applied.

        :param: value: A value to be parsed and potentially converted to a boolean.
        :return: The parsed value if conversion is successful, otherwise the original value.
        """
        if isinstance(value, str) and value.lower() in ("false", "off", 0):
            value = False
        elif isinstance(value, str) and value.lower() in ("true", "on", 1):
            value = True
        return value

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
            row["current value"] = self._parse_value(row["current value"])
            setattr(journal.submissionconfiguration, row["field name"], row["current value"])
        journal.submissionconfiguration.save()
        return csv_rows

    def _set_submission_fields(self, journal: Journal, csv_rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Set submission fields from CSV rows."""
        Field.objects.filter(journal=journal).all().delete()
        applied_settings = []
        for index, row in enumerate(csv_rows):
            field = Field.objects.create(
                journal=journal,
                name=row["name"],
                kind=row.get("kind", "text"),
                width=row.get("width", "full"),
                required=row.get("required", True),
                choices=row.get("choices", ""),
                help_text=row["help_text"],
                order=index,
                display=True,
            )
            applied_settings.append(field)
        return applied_settings

    def _set_settings(self, journal: Journal, csv_rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Set settings from CSV rows."""
        applied_settings = []
        for row in csv_rows:
            if row["value"] and not row["value"].startswith("NOT USED"):
                value = self._parse_value(row["value"])
                save_setting(setting_group_name=row["group"], setting_name=row["name"], journal=journal, value=value)
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
        elif options["group_name"] == "submissionfields":
            applied_settings = self._set_submission_fields(journal, csv_rows)
        else:
            applied_settings = self._set_settings(journal, csv_rows)
        if options["verbosity"] > 1:
            settings = "\n - ".join([f'{setting["name"]}: {setting["value"]}' for setting in applied_settings])
            sys.stdout.write(f"Settings updated\n=========\n{settings}")
