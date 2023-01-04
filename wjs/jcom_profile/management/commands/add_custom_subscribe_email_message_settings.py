"""Management command to add custom email settings."""

from core.models import Setting, SettingGroup, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import ugettext_lazy as _


class Command(BaseCommand):
    help = "Create custom email settings (body message)"  # NOQA

    def _create_custom_email_messages(self, group: SettingGroup):
        subscribe_custom_email_message, setting_created = Setting.objects.get_or_create(
            name="subscribe_custom_email_message",
            group=group,
            types="text",
            pretty_name=_("Email message that is sent when an anonymous user subscribes to newsletters."),
            description=_(
                "Message email body",
            ),
            is_translatable=False,
        )
        try:
            SettingValue.objects.get(journal=None, setting=subscribe_custom_email_message)
            self.stdout.write(
                self.style.WARNING("'Subscribe custom email message' setting already exists. Do nothing.")
            )
        except SettingValue.DoesNotExist:
            SettingValue.objects.create(
                journal=None,
                setting=subscribe_custom_email_message,
                value="Hi,\nYou requested to subscribe to {} journal newsletters.\n"
                "To continue click the following link:{}",
            )
            self.stdout.write(self.style.SUCCESS("Successfully created subscribe_custom_email_message setting."))

    def _get_group(self, name: str) -> SettingGroup:
        try:
            return SettingGroup.objects.get(name=name)
        except SettingGroup.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"{name} group does not exist."))
            return None

    def handle(self, *args, **options):
        """Command entry point."""
        general_settings_group = self._get_group("email")

        if general_settings_group:
            self._create_custom_email_messages(general_settings_group)
        else:
            self.stdout.write(self.style.ERROR("Check out your groups (general) settings before."))
