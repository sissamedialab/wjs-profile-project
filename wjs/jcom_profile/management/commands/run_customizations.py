from django.core import management
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = """Run all customizations implemented as django-admin commands. This implementation is chosen
           "to avoid conflicts with original janaway installations"""

    def handle(self, *args, **options):
        management.call_command('add_coauthors_submission_email_settings')
