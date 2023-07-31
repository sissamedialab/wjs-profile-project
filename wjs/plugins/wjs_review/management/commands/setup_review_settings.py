from django.core.management.base import BaseCommand

from ...plugin_settings import set_default_plugin_settings


class Command(BaseCommand):
    help = "Setup wjs_review settings."  # noqa

    def handle(self, *args, **options):
        set_default_plugin_settings()
