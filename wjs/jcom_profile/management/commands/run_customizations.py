"""Management command to add all customizations."""


from django.core import management
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run all customizations implemented as django-admin commands"  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        management.call_command("add_submission_figures_data_title")
        management.call_command("add_coauthors_submission_email_settings")
        management.call_command("add_user_as_main_author_setting")
        management.call_command("install_themes")
        management.call_command("link_plugins")
        management.call_command("create_role", "Director")
        management.call_command("add_publication_alert_settings")
        management.call_command("add_generic_analytics_code_setting")
