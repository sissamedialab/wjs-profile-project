"""Management command to add custom email settings."""
from typing import Optional

from core.models import Setting, SettingGroup, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _


class Command(BaseCommand):
    help = "Create custom email settings (body message)"  # NOQA

    def _create_setting(
        self,
        group: SettingGroup,
        setting_name: str,
        setting_description: str,
        pretty_name: str,
        default_value: str,
        field_type: str = "text",
        is_translatable: bool = False,
    ):
        setting_obj, setting_created = Setting.objects.get_or_create(
            name=setting_name,
            group=group,
            types=field_type,
            pretty_name=pretty_name,
            description=setting_description,
            is_translatable=is_translatable,
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
                is_translatable=True,
                field_type="rich-text",
                default_value="""
Hello,
<p>
We have received a request to subscribe your email address to JCOM publication alert.
</p>
<p>
To confirm your email address, activate your subscription and select your topics of interest click on <a href="{acceptance_url}" target="_blank">this link</a>
</p>
<p>
By clicking the above link you are agreeing to our <a href="https://medialab.sissa.it/en/privacy">privacy policy</a>.<br>
You can unsubscribe at any time by using the link provided in every publication alert that you will receive.
</p>
<p>
If you did not request to subscribe, you do not need to do anything. If you do not click on the activation link,
you will not be added to our mailing list.
</p>
<p>
Kind regards,
</p>
<p>
JCOM - Journal of Science Communication
</p>
""",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_subscription_email_subject",
                setting_description="Email subject",
                pretty_name="Subject of the email sent when an anonymous user subscribes to publication alert.",
                is_translatable=True,
                default_value="Publication alert subscription",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_reminder_email_body",
                setting_description="Email body",
                pretty_name="Body of the email sent when an anon user subscribes to an alert that is already subscribed to",  # noqa: E501
                is_translatable=True,
                field_type="rich-text",
                default_value="""
Hello,
<p>
We have received a request to subscribe your email address to JCOM publication alert.
</p>
<p>
Please note that you are already subscribed. If you wish to change your topics of interest use the link below.
</p>
<p>
<a href="{acceptance_url}">Change topics of interest</a>
</p>
<p>
Kind regards,
</p>
<p>
JCOM - Journal of Science Communication
</p>
""",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_reminder_email_subject",
                setting_description="Email subject",
                pretty_name="Subject of the email sent when an anon user subscribes to an alert that is already subscribed to",  # noqa: E501
                is_translatable=True,
                default_value="Your subscription to JCOM publication alert",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_email_intro_message",
                setting_description="Email introduction message",
                pretty_name="Introduction to the publication alert body.",
                is_translatable=True,
                default_value="See current news",
            )
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_email_subject",
                setting_description="Email subject",
                pretty_name="Subject of the publication alert email.",
                is_translatable=True,
                default_value="Publication alert subscription",
            )
        else:
            self.stdout.write(self.style.ERROR("Check out your groups (general) settings before."))
