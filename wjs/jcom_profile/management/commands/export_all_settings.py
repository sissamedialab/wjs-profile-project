import csv
from dataclasses import dataclass

from core.models import SettingValue
from django.core.management.base import BaseCommand


@dataclass
class Setting:
    name: str
    group: str
    description: str
    translatable: bool
    default: str = ""
    jcom: str = ""
    jcomal: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "translatable": self.translatable,
            "default value": self.default,
            "jcom value": self.jcom,
            "jcomal value": self.jcomal,
        }


class Command(BaseCommand):
    help = "Export all text settings to a csv file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            help="The path to the csv file to save.",
            default="/tmp/all_settings.csv",
        )
        parser.add_argument(
            "--translatable-only",
            action="store_true",
            help="Collect only settings that are translatable.",
        )

    def handle(self, *args, **options):
        """Entry point."""
        # Here we collect all text-like settings values, both default (setting__journal == None) and the journals overrides.
        # We will re-organize the values later, in order to have a structure suitable to the csv headers
        setting_values = SettingValue.objects.filter(
            setting__types__in=["rich-text", "mini-html", "text", "char"],
        )
        if options["translatable_only"]:
            setting_values = setting_values.filter(
                setting__is_translatable=True,
            )
        setting_values = setting_values.values(
            "setting__name",
            "setting__group__name",
            "setting__description",
            "setting__is_translatable",
            "journal__code",
            "value",
        )

        csv_headers = ["name", "group", "description", "translatable", "default value", "jcom value", "jcomal value"]

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

            if value["journal__code"] is None:
                setting.default = value["value"]
            elif value["journal__code"] == "JCOM":
                setting.jcom = value["value"]
            elif value["journal__code"] == "JCOMAL":
                setting.jcomal = value["value"]
            else:
                raise Error(f"Unexpected journal code {value['journal__code']}")

        with open(options["output"], mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
            writer.writeheader()
            for key, setting in rows.items():
                writer.writerow(setting.to_dict())
