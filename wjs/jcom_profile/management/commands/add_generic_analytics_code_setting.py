"""Management command to add analitics code setting."""
from typing import Optional

from core.models import Setting, SettingGroup, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _


class Command(BaseCommand):
    help = "Create analitics code settings"  # NOQA

    def _create_setting(
        self,
        group: SettingGroup,
        setting_name: str,
        setting_description: str,
        pretty_name: str,
        default_value: str,
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
        general_settings_group = self._get_group("general")

        if general_settings_group:
            self._create_setting(
                group=general_settings_group,
                setting_name="analytics_code",
                setting_description=_(
                    "Code added to every page of the journal in order to track visits and analytics."
                    " E.g. Google Analitics or Matomo complete tracking code."
                    " Not just the site code 🙂",
                ),
                pretty_name=_("Analytics tracking code"),
                default_value="",
            )
        else:
            self.stdout.write(self.style.ERROR("Check out your groups (general) settings before."))
