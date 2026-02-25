import csv
import sys

from django.core.management.base import BaseCommand, CommandError
from journal.models import Journal
from submission.models import Field


class Command(BaseCommand):
    help = "Dump the values of submission.models.Field for a specific journal in a csv"  # noqa A003

    def add_arguments(self, parser):
        parser.add_argument("journal_code", type=str, help="The code of the journal")
        parser.add_argument("--output", type=str, help="The output path for the CSV file")

    def handle(self, *args, **options):
        journal_code = options["journal_code"]
        output_path = options["output"]

        try:
            journal = Journal.objects.get(code=journal_code)
        except Journal.DoesNotExist:
            raise CommandError(f'Journal with code "{journal_code}" does not exist')

        fields = Field.objects.filter(journal=journal)

        if output_path:
            f = open(output_path, "w", newline="")
        else:
            f = sys.stdout

        writer = csv.writer(f)
        writer.writerow(["name", "help_text", "current value"])

        for field in fields:
            writer.writerow([field.name, field.help_text, ""])

        if output_path:
            f.close()
            self.stdout.write(self.style.SUCCESS(f"Successfully dumped fields to {output_path}"))
