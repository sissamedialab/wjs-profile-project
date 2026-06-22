"""Management command to add all customizations."""

from django.core import management
from django.core.management.base import BaseCommand
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Run all customizations implemented as django-admin commands"  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        management.call_command("create_custom_settings")
        management.call_command("install_themes")
        management.call_command("link_plugins")
        management.call_command("create_role", "Director")
        management.call_command("apply_wjs_settings", "--noinput")
        management.call_command("enable_hierarchical_keywords")
