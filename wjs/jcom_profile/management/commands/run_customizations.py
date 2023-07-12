"""Management command to add all customizations."""


from django.core import management
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run all customizations implemented as django-admin commands"  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        management.call_command("create_custom_settings")
        management.call_command("install_themes")
        management.call_command("link_plugins")
        management.call_command("create_role", "Director")
