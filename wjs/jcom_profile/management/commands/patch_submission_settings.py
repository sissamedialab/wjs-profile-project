"""Management command to add all custom settings"""

from django.core.management.base import BaseCommand
from journal.models import Journal

from wjs.jcom_profile.custom_settings_utils import (
    add_submission_settings,
    export_to_csv_manager,
)


class Command(BaseCommand):
    help = "Update journal settings for submission"

    def handle(self, *args, **options):
        with export_to_csv_manager("jcom_profile") as csv_writer:
            for journal in Journal.objects.filter(code="JCOM"):
                csv_writer.write_settings(add_submission_settings(journal))
