"""Management command to add all custom settings"""

from django.core.management.base import BaseCommand

from wjs.jcom_profile.custom_settings_utils import (
    add_coauthors_submission_email_settings,
    add_general_facebook_handle_setting,
    add_generic_analytics_code_setting,
    add_publication_alert_settings,
    add_submission_figures_data_title,
    add_user_as_main_author_setting,
)


class Command(BaseCommand):
    help = "Create custom settings"

    def handle(self, *args, **options):
        add_submission_figures_data_title()
        add_coauthors_submission_email_settings()
        add_user_as_main_author_setting()
        add_publication_alert_settings()
        add_generic_analytics_code_setting()
        # refs specs#640
        add_general_facebook_handle_setting()
