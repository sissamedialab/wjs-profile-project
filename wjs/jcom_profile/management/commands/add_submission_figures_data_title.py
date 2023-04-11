"""Management command to add settings for coauthors' emails."""

from core.models import Setting, SettingGroup, SettingValue
from django.core.management.base import BaseCommand
from django.utils.translation import ugettext_lazy as _


class Command(BaseCommand):
    help = "Create subject and template of the email to be sent to coauthors article submission."

    def handle(self, *args, **options):
        """Command entry point."""
        # TODO: other choices: general | app:wjs
        styling_settings_group = SettingGroup.objects.get(name="styling")

        submission_figures_data_title = Setting.objects.create(
            name="submission_figures_data_title",
            group=styling_settings_group,
            types="rich-text",
            pretty_name=_("Files Submission - Title of Figures and Data Files Field"),
            description=_("Displayed on the Files Submission page."),
            is_translatable=True,
        )

        SettingValue.objects.create(
            journal=None,
            setting=submission_figures_data_title,
            value="Figures and Data Files",
            value_cy="Ffigurau a Ffeiliau Data",
            value_de="Abbildungen und Datenfiles",
            value_en="Figures and Data Files",
            value_fr="Figures et dossiers de données",
            value_nl="Figuren en gegevensbestanden",
        )
