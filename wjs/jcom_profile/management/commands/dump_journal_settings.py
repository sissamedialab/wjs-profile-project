import csv
import sys

from core.models import SettingValue
from django.core.management.base import BaseCommand, CommandError
from journal.models import Journal


class Command(BaseCommand):
    help = "Dump the SettingValue for a specific journal in a csv"  # noqa A003

    def add_arguments(self, parser):
        parser.add_argument("journal_code", type=str, help="The code of the journal")
        parser.add_argument("--output", type=str, help="The output path for the CSV file")
        parser.add_argument(
            "--only-different",
            action="store_true",
            help="Dump only the setting value with a value different from default",
        )

    def handle(self, *args, **options):
        journal_code = options["journal_code"]
        output_path = options["output"]
        only_different = options["only_different"]

        try:
            journal = Journal.objects.get(code=journal_code)
        except Journal.DoesNotExist:
            raise CommandError(f'Journal with code "{journal_code}" does not exist')

        setting_values = SettingValue.objects.filter(journal=journal).select_related("setting", "setting__group")

        if output_path:
            f = open(output_path, "w", newline="")
        else:
            f = sys.stdout

        writer = csv.writer(f)
        writer.writerow(["group", "setting", "value", "default_value", "is_different"])

        for sv in setting_values:
            try:
                default_sv = SettingValue.objects.get(setting=sv.setting, journal=None)
                default_value = default_sv.value
            except SettingValue.DoesNotExist:
                default_value = None

            is_different = sv.value != default_value

            if only_different and not is_different:
                continue

            writer.writerow([sv.setting.group.name, sv.setting.name, sv.value, default_value, is_different])

        if output_path:
            f.close()
            self.stdout.write(self.style.SUCCESS(f"Successfully dumped settings to {output_path}"))
