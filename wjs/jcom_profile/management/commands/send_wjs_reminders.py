"""A command to send WJS reminders.

NB: Janeway also have a command called "send_reminders". It deals with Janeway's original reminders implementation.

"""

from django.core.management.base import BaseCommand
from django.utils import timezone

# NB: explicit relative imports for plugins stuff does not work:
#     e.g.: from ....plugins.wjs_review.models import Message
from plugins.wjs_review.models import Reminder
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Send due reminders. This command is intended to be used via a cron task."  # noqa

    def add_arguments(self, parser):
        # TODO: do we want to filter/send reminders per-journal?
        # e.g.: parser.add_argument("journal")
        pass

    def handle(self, *args, **options):
        """Send reminders."""
        reminders = Reminder.objects.filter(
            disabled=False,
            date_sent__isnull=True,
            date_due__lt=timezone.now().date(),
            # TODO: journal? see above...
        )
        sent_reminders = 0
        for reminder in reminders:
            try:
                message = reminder.create_message()
            except ValueError as e:
                logger.error(f"Error creating message for reminder {reminder.id} ({reminder.code}): {e}")
                continue
            message.emit_notification(from_email=reminder.get_from_email())
            reminder.date_sent = message.created
            reminder.save()
            sent_reminders += 1
        logger.debug(f"Sent {sent_reminders}/{reminders.count()} reminders.")
