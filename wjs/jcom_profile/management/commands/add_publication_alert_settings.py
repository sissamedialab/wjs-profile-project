"""Management command to add custom email settings."""
from typing import Optional

from core.models import Setting, SettingGroup, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import ugettext_lazy as _


class Command(BaseCommand):
    help = "Create custom email settings (body message)"  # NOQA

    def _create_setting(
        self, group: SettingGroup, setting_name: str, setting_description: str, pretty_name: str, default_value: str,
            field_type: str = "text"
    ):
        setting_obj, setting_created = Setting.objects.get_or_create(
            name=setting_name,
            group=group,
            types=field_type,
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
            try:
                # Temporary instruction to migrate existing installations. It can be removed after go live
                Setting.objects.get(
                    types="text",
                    group=general_settings_group,
                    name="publication_alert_subscription_email_body",
                ).delete()
            except Setting.DoesNotExist:
                pass
            self._create_setting(
                group=general_settings_group,
                setting_name="publication_alert_subscription_email_body",
                setting_description="Email body",
                pretty_name="Body of the email sent when an anonymous user subscribes to publication alert.",
                field_type="rich-text",
                default_value="""
Hello,
<p>
We have received a request to subscribe the email address leo@medialab.sissa.it to JCOM publication alert.
To confirm your email address, activate your subscription and select your topics of interest click on the link below:
</p>
<p>
<a href="{acceptance_url}">{acceptance_url}</a<
</p>
<p>
By clicking the above link you are agreeing to our <a href="https://medialab.sissa.it/en/privacy">privacy policy</a>.
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
                default_value="JCOM alert confirmation",
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
