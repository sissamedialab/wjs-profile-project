"""Correct existing settings names.

Una-tantum command needed because of name changed during specs#901.
"""

from core.models import Setting
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Correct existing settings names."  # noqa A003

    def handle(self, *args, **options):
        settings_to_rename = (
            (
                "submission_coauthors_acknowledgment",
                "submission_coauthors_acknowledgement_body",  # Please also note the "e" in "acknowledgEment"
            ),
            (
                "subject_submission_coauthors_acknowledgement",
                "submission_coauthors_acknowledgement_subject",
            ),
        )

        for old_name, new_name in settings_to_rename:
            # I don't want to use `update` because it is designed for bulk-operations
            # and I want to be sure that I'm operating on a single setting
            self.stdout.write(f"Correcting {old_name} into {new_name}")
            setting = Setting.objects.get(name=old_name)
            setting.name = new_name
            setting.save()
