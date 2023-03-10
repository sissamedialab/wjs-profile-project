"""Management command to add custom email settings."""
from typing import Optional

from core.models import Setting, SettingGroup, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import ugettext_lazy as _


class Command(BaseCommand):
    help = "Create custom email settings (body message)"  # NOQA

    def _create_setting(
        self, group: SettingGroup, setting_name: str, setting_description: str, pretty_name: str, default_value: str
    ):
        setting_obj, setting_created = Setting.objects.get_or_create(
            name=setting_name,
            group=group,
            types="text",
            pretty_name=pretty_name,
            description=setting_description,
            is_translatable=False,
        )
        try:
            SettingValue.objects.get(journal=None, setting=setting_obj)
            self.stdout.write(self.style.WARNING(f"'{setting_name}' setting already exists. Do nothing."))
        except SettingValue.DoesNotExist:
            SettingValue.objects.create(journal=None, setting=setting_obj, value=default_value)
            self.stdout.write(self.style.SUCCESS(f"'{setting_name}' setting successfully created."))

    def _get_group(self, name: str) -> Optional[SettingGroup]:
        try:
            return SettingGroup.objects.get(name=name)
        except SettingGroup.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"{name} group does not exist."))
            return None

    def handle(self, *args, **options):
        """Command entry point."""
        general_settings_group = self._get_group("email")

        if general_settings_group:
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_subscription_email_body",
                setting_description="Email body",
                pretty_name="Body of the email sent when an anonymous user subscribes to publication alert.",
                default_value="Hi,\nYou requested to subscribe to {} journal newsletters.\n"
                "To continue click the following link:{}",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_subscription_email_subject",
                setting_description="Email subject",
                pretty_name="Subject of the email sent when an anonymous user subscribes to publication alert.",
                default_value="Publication alert subscription",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_email_intro_message",
                setting_description="Email introduction message",
                pretty_name="Introduction to the publication alert body.",
                default_value="See current news",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_email_subject",
                setting_description="Email subject",
                pretty_name="Subject of the publication alert email.",
                default_value="{journal} - Publication alert subscription - {date}",
            )
        else:
            self.stdout.write(self.style.ERROR("Check out your groups (general) settings before."))
