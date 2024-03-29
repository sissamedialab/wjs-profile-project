from core.models import Account
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django_q.tasks import async_task


def test_send_email(user_id: int):
    """Send test email as a sample django-q task."""

    user = Account.objects.get(pk=user_id)

    send_mail(
        "Subject here",
        "Here is the message.",
        "i.spalletti@nephila.digital",
        [user.email],
    )
    return "ciao"


def print_result(task):
    """Print the result of django-q task."""
    print("Task result: ", task.result)
    print(task.__dict__)


class Command(BaseCommand):
    help = "Command to send a sample task."  # noqa: A003

    def handle(self, *args, **options):
        user = Account.objects.all().first()

        task_id = async_task(
            test_send_email,
            user.pk,
            hook="wjs.jcom_profile.management.commands.test-djangoq2.print_result",
        )
        print("Task id: ", task_id)
