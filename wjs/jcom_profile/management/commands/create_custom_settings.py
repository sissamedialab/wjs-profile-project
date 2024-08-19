"""Management command to add all custom settings"""

from django.core.management.base import BaseCommand

from wjs.jcom_profile.custom_settings_utils import (
    add_coauthors_submission_email_settings,
    add_general_facebook_handle_setting,
    add_generic_analytics_code_setting,
    add_publication_alert_settings,
    add_submission_figures_data_title,
    add_user_as_main_author_setting,
    export_to_csv_manager,
)


class Command(BaseCommand):
    help = "Create custom settings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force the setup of the settings.",
        )

    def handle(self, *args, **options):
        with export_to_csv_manager("jcom_profile") as csv_writer:
            csv_writer.write_settings(add_submission_figures_data_title(force=options["force"]))
            csv_writer.write_settings(add_coauthors_submission_email_settings(force=options["force"]))
            csv_writer.write_settings(add_user_as_main_author_setting(force=options["force"]))
            csv_writer.write_settings(add_publication_alert_settings(force=options["force"]))
            csv_writer.write_settings(add_generic_analytics_code_setting(force=options["force"]))
            # refs specs#640
            csv_writer.write_settings(add_general_facebook_handle_setting(force=options["force"]))
