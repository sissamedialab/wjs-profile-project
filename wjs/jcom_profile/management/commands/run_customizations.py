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
        try:
            management.call_command("setup_review_settings")
        except management.CommandError:
            # if it's not installed, we don't care and we just skip it
            logger.debug("wjs_review is not installed")
