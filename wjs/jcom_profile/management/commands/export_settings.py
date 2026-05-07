import csv
from dataclasses import dataclass

from core.models import SettingValue
from django.core.management.base import BaseCommand
from django.db.models import Q
from submission.models import SubmissionConfiguration


@dataclass
class Setting:
    name: str
    group: str
    description: str
    translatable: bool
    default: str = ""
    value: str = ""
    journal: str = ""

    def to_dict(self):
        return {
            "journal": self.journal,
            "name": self.name,
            "group": self.group,
            "value": self.value,
            "default value": self.default,
            "description": self.description,
            "translatable": self.translatable,
        }


@dataclass
class Configuration:
    name: str
    verbose: str
    default: str
    value: str
    origin: str

    def to_dict(self):
        return {
            "field name": self.name,
            "field verbose name": self.verbose,
            "default value": self.default,
            "current value": self.value,
            "origin": self.origin,
        }


class Command(BaseCommand):
    help = "Export all journal settings to a csv file."

    def add_arguments(self, parser):
        parser.add_argument(
            "journal",
            help="Journal code",
        )
        parser.add_argument(
            "--group",
            help="Optional group name to filter journals on",
        )
        parser.add_argument(
            "--output",
            "-o",
            help="The path to the csv file to save.",
            default="/tmp/all_settings.csv",
        )

    def _export_settings(self, journal, group=""):
        """
        Extract all the setting values

        It extracts either with a journal (the overrides) and without (the defaults) ordered
        by journal value (ie: the defaults come last)
        Later the first value is taken into account so the override will take precedence over the default
        """
        setting_values = SettingValue.objects.filter(
            Q(journal__code=journal) | Q(journal__isnull=True),
        )
        if group:
            setting_values = setting_values.filter(setting__group__name=group)
        setting_values = setting_values.values(
            "setting__name",
            "setting__group__name",
            "setting__description",
            "setting__is_translatable",
            "journal__code",
            "value",
        ).order_by("-journal", "setting__group__name", "setting__name")

        csv_headers = ["journal", "name", "group", "value", "description", "translatable", "default value"]

        rows = {}
        for value in setting_values:
            key = f'{value["setting__group__name"]}/{value["setting__name"]}'
            if key in rows:
                setting = rows[key]
            else:
                setting = Setting(
                    group=value["setting__group__name"],
                    name=value["setting__name"],
                    description=value["setting__description"],
                    translatable=value["setting__is_translatable"],
                )
                rows[key] = setting

            setting.value = value["value"]
            setting.journal = value["journal__code"]
        return rows, csv_headers

    def _export_submissionconfiguration(self, journal):
        """
        Extract all the setting values

        It extracts either with a journal (the overrides) and without (the defaults) ordered
        by journal value (ie: the defaults come last)
        Later the first value is taken into account so the override will take precedence over the default
        """
        configuration = SubmissionConfiguration.objects.get(journal__code=journal)
        csv_headers = ["field name", "field verbose name", "current value", "default value", "origin"]

        rows = {}
        for field in configuration._meta.get_fields():
            if field.name not in ("id", "journal"):
                setting = Configuration(
                    name=field.name,
                    verbose=str(field.verbose_name),
                    default=field.default,
                    value=getattr(configuration, field.name),
                    origin="SubmissionConfiguration",
                )
                rows[field.name] = setting
        return rows, csv_headers

    def handle(self, *args, **options):
        """Entry point."""

        if options["group"] == "submissionconfiguration":
            rows, csv_headers = self._export_submissionconfiguration(options["journal"])
        else:
            rows, csv_headers = self._export_settings(options["journal"], options["group"])

        with open(options["output"], mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
            writer.writeheader()
            for key, setting in rows.items():
                writer.writerow(setting.to_dict())
        self.stdout.write(self.style.SUCCESS(f"Successfully dumped fields to {options['output']}"))
