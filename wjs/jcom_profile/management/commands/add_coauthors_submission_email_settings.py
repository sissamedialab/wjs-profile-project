from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext_lazy as _

from core.models import Setting, SettingValue, SettingGroup


class Command(BaseCommand):
    help = "Create subject and template of the email to be sent to coauthors article submission."

    def _create_coauthors_submission_email_template_setting(self, group: SettingGroup):
        coauthor_submission_email_template, email_created = Setting.objects.get_or_create(
            name="submission_coauthors_acknowledgment",
            group=group,
            types="rich-text",
            pretty_name=_("Submission Coauthors Acknowledgement"),
            description=_("Email sent to coauthors when they have submitted an article."),
            is_translatable=True
        )
        try:
            SettingValue.objects.get(journal=None,
                                     setting=coauthor_submission_email_template)
            self.stdout.write(
                self.style.WARNING(
                    "Email template setting for coauthor submission notification already exists. Do nothing."))
        except SettingValue.DoesNotExist:
            SettingValue.objects.create(
                journal=None,
                setting=coauthor_submission_email_template,
                value="Dear {{ author.full_name}}, <br><br>Thank you for submitting \"{{ article }}\" to {{ article.journal }} as coauthor.<br><br> Your work will now be reviewed by an editor and we will be in touch as the peer-review process progresses.<br><br>Regards,<br>",
                value_en="Dear {{ author.full_name}}, <br><br>Thank you for submitting \"{{ article }}\" to {{ article.journal }} as coauthor.<br><br> Your work will now be reviewed by an editor and we will be in touch as the peer-review process progresses.<br><br>Regards,<br>"
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Successfully created coauthor email template setting for submission notification."))

    def _create_coauthors_submission_email_subject_setting(self, group: SettingGroup):
        coauthor_submission_email_subject, subject_created = Setting.objects.get_or_create(
            name="subject_submission_coauthors_acknowledgement",
            group=group,
            types="text",
            pretty_name=_("Submission Subject Coauthors Acknowledgement"),
            description=_("Subject for Email sent to coauthors when they have submitted an article."),
            is_translatable=True
        )
        try:
            SettingValue.objects.get(journal=None,
                                     setting=coauthor_submission_email_subject)
            self.stdout.write(
                self.style.WARNING(
                    "Email subject setting for coauthor submission notification already exists. Do nothing."))

        except SettingValue.DoesNotExist:
            SettingValue.objects.create(
                journal=None,
                setting=coauthor_submission_email_subject,
                value="Coauthor - Article Submission",
                value_en="Coauthor - Article Submission"
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Successfully created coauthor email subject setting for submission notification."))

    def _get_group(self, name: str) -> SettingGroup:
        try:
            return SettingGroup.objects.get(name=name)
        except SettingGroup.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"{name} group does not exist."))
            return None

    def handle(self, *args, **options):
        email_settings_group = self._get_group("email")
        email_subject_settings_group = self._get_group('email_subject')

        if email_settings_group and email_subject_settings_group:
            self._create_coauthors_submission_email_template_setting(email_settings_group)
            self._create_coauthors_submission_email_subject_setting(email_subject_settings_group)
        else:
            # TODO: Create an ad hoc command to handle this case? I don't know if it could happen.
            self.stdout.write(
                self.style.ERROR(f"Check out your groups (email and email_subjects) settings before."))
