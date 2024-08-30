"""A command to send WJS reminders.

NB: Janeway also have a command called "send_reminders". It deals with Janeway's original reminders implementation.

"""

import inspect

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils import timezone

# NB: explicit relative imports for plugins stuff does not work:
#     e.g.: from ....plugins.wjs_review.models import Message
from plugins.wjs_review.models import Message, MessageRecipients, Reminder
from plugins.wjs_review.reminders import settings as reminders_settings
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

        # The `mark_as_read...` flags are defined only in the reminders' settings, not in the reminders objects
        # themselves. Also, there is no direct link from reminder to setting except via the reminder's `code`. So here
        # we build a dictionary of all the reminders settings and organize it with the `code`, so that we have an easy
        # way to get from the reminder object ot its setting.
        settings_by_code = {}
        reminders_managers = [
            name
            for name, cls in inspect.getmembers(reminders_settings, inspect.isclass)
            if name.endswith("ReminderManager") and cls is not reminders_settings.ReminderManager
        ]
        for manager in reminders_managers:
            settings_by_code.update(**getattr(reminders_settings, manager).reminders)

        sent_reminders = 0
        for reminder in reminders:
            reminder_article = reminder.get_related_article()
            if reminder_article is None:
                logger.error(f"Unknown article for reminder {reminder.id} ({reminder.code})")
                continue

            message = Message.objects.create(
                actor=reminder.actor,
                subject=reminder.message_subject,
                body=reminder.message_body,
                content_type=ContentType.objects.get_for_model(reminder_article),
                object_id=reminder_article.id,
                read_by_eo=settings_by_code[reminder.code].flag_as_read_by_eo,
            )
            message.recipients.add(reminder.recipient)
            if settings_by_code[reminder.code].flag_as_read:
                MessageRecipients.objects.filter(message=message).update(read=True)

            message.emit_notification(from_email=reminder.get_from_email())

            reminder.date_sent = message.created
            reminder.save()
            sent_reminders += 1
        logger.debug(f"Sent {sent_reminders}/{reminders.count()} reminders.")
