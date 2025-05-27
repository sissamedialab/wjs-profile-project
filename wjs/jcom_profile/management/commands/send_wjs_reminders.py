"""A command to send WJS reminders.

NB: Janeway also have a command called "send_reminders". It deals with Janeway's original reminders implementation.

"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

# NB: explicit relative imports for plugins stuff does not work:
#     e.g.: from ....plugins.wjs_review.models import Message
from plugins.wjs_review.models import Reminder
from utils.logger import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Send due reminders. This command is intended to be used via a cron task."  # noqa

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the creation of te messages, but don't send nor save anything",
        )
        # TODO: do we want to filter/send reminders per-journal?
        # e.g.: parser.add_argument("journal")
        # But! remember that reminders have no direct relation to Journal!
        # (although they have a "target" that is usually related to the journal)

    def handle(self, *args, **options):
        """Send reminders."""
        reminders = Reminder.objects.filter(
            disabled=False,
            date_sent__isnull=True,
            date_due__lte=timezone.now().date(),
        )

        # If the expedition of reminders is delayed for several days, reminders for the same recipient/target/"reason"
        # can "pile-up". If this happens, we send only the first. The rest will be sent the next time that this command
        # is run.
        seen_reminders = {}
        sent_reminders = 0
        for reminder in reminders:
            try:
                message = reminder.create_message()
            except ValueError:
                logger.exception(f"Error creating message for reminder {reminder.id} ({reminder.code})")
                continue
            key = (reminder.target.id, reminder.recipient.id, reminder.code[:-1])
            if key in seen_reminders:
                logger.info(
                    f"Skipping {reminder.code} to {reminder.recipient} for {message.target.id}:"
                    f' "{message.subject.replace("Reminder: ", "")[:17]}…"',
                )
                continue

            seen_reminders[key] = True
            if not options["dry_run"]:
                with transaction.atomic():
                    message.save()
                    message.recipients.set([reminder.recipient])
                    message.emit_notification(from_email=reminder.get_from_email())
                    reminder.date_sent = message.created
                    reminder.save()
            else:
                logger.debug(
                    f"Dry-run {reminder.code} to {reminder.recipient} for {message.target.id}:"
                    f' "{message.subject.replace("Reminder: ", "")[:17]}…"',
                )
            sent_reminders += 1
        logger.debug(f"Sent {sent_reminders}/{reminders.count()} reminders.")
