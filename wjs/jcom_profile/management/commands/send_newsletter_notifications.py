from django.core.management.base import BaseCommand
from ...newsletter.service import SendNewsletter

import logging

logger = logging.getLogger("wjs.newsletter")

class Command(BaseCommand):
    help = "Send newsletter to enrolled users. This command is intended to be used via a cron task."  # noqa

    def add_arguments(self, parser):
        parser.add_argument("journal")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        """Run SendNewsletter service to send newsletter"""
        messages = SendNewsletter().render_and_send(options["journal"], options["force"])
        for message in messages:
            logger.debug(message)
