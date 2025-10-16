from core.models import Account
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Fix ORCID format in user attributes."  # noqa

    def handle(self, *args, **options):
        for a in Account.objects.filter(orcid__isnull=False, orcid__startswith="http"):
            a.orcid = a.orcid.split("/")[-1]
            a.save()
