"""Correct existing settings names.

Una-tantum command needed because of name changed during specs#901.
"""

from core.models import Setting
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Correct existing settings names."  # noqa A003

    def handle(self, *args, **options):
        self.rename_settings()
        self.drop_obsolete_settings()

    def rename_settings(self):
        settings_to_rename = (
            # jcom_profile
            (
                "submission_coauthors_acknowledgment",
                "submission_coauthors_acknowledgement_body",  # Please also note the "e" in "acknowledgEment"
            ),
            (
                "subject_submission_coauthors_acknowledgement",
                "submission_coauthors_acknowledgement_subject",
            ),
            # wjs_review
            (
                "review_invitation_message",
                "review_invitation_message_default",
            ),
            (
                "declined_review_message",
                "declined_review_notice",
            ),
            (
                "review_withdraw_notice",
                "review_withdraw_default",
            ),
            (
                "revision_submission_message",
                "revision_submission_body",
            ),
            (
                "requeue_article_message",
                "requeue_article_body",
            ),
            (
                "review_decision_requires_resubmission_message",
                "review_decision_requires_resubmission_body",
            ),
            (
                "editor_decline_assignment_body",
                "editor_decline_assignment_default",
            ),
            (
                "editor_deassign_reviewer_body",
                "editor_deassign_reviewer_default",
            ),
            # Do not correct the followings Janeway settings: we simply add our owns
            # - subject_editor_assignment ⬄ wjs_editor_assignment_subject
            # - editor_assignment ⬄ wjs_editor_assignment_body
            # - review_assignment ⬄ review_invitation_message_body + review_invitation_message_default
            # - subject_review_assignment ⬄ review_invitation_message_subject
        )

        for old_name, new_name in settings_to_rename:
            # I don't want to use `update` because it is designed for bulk-operations
            # and I want to be sure that I'm operating on a single setting
            self.stdout.write(f"Correcting {old_name} into {new_name}")
            try:
                setting = Setting.objects.get(name=old_name)
            except Setting.DoesNotExist:
                Setting.objects.get(name=new_name)
                self.stdout.write(f"   setting {new_name} already in place. Doing nothing.")
            else:
                setting.name = new_name
                setting.save()

    def drop_obsolete_settings(self):
        """Drop settings we had second thoughts about."""
        settings_to_drop = (
            "editor_deassign_reviewer_system_subject",
            "editor_deassign_reviewer_system_body",
            "review_withdraw_body",
        )
        for setting_name in settings_to_drop:
            self.stdout.write(f"Dropping {setting_name}")
            group_name = "wjs_review"
            try:
                setting = Setting.objects.get(name=setting_name, group__name=group_name)
            except Setting.DoesNotExist:
                self.stdout.write(f"   setting {setting_name} in group {group_name} does not exist. Doing nothing.")
            else:
                setting.delete()
