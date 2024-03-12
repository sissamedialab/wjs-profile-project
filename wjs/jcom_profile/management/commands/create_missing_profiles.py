from core.models import Account
from django.core.management.base import BaseCommand

from wjs.jcom_profile.models import JCOMProfile


class Command(BaseCommand):
    help = "Create the corresponding JCOMProfile model for each Account instance which has not one."  # noqa A003

    def handle(self, *args, **options):
        for account in Account.objects.filter(jcomprofile__isnull=True):
            JCOMProfile(janeway_account=account).save_base(raw=True)
            self.stdout.write(f"Created JCOMProfile for {account}")
