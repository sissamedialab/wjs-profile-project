import csv
from dataclasses import dataclass

from core.models import SettingValue
from django.core.management.base import BaseCommand
from django.db.models import Q


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

    def handle(self, *args, **options):
        """Entry point."""
        # Extracting all the settin values, either with a journal (the overrides) and without (the defaults) ordered
        # by journal value (ie: the defaults come last)
        # Later the first value is taken into account so the override will take precedence over the default
        setting_values = SettingValue.objects.filter(
            Q(journal__code=options["journal"]) | Q(journal__isnull=True),
        )
        if options["group"]:
            setting_values = setting_values.filter(setting__group__name=options["group"])
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

        with open(options["output"], mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
            writer.writeheader()
            for key, setting in rows.items():
                writer.writerow(setting.to_dict())
