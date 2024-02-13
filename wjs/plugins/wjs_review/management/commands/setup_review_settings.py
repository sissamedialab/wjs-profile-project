from django.core.management.base import BaseCommand

from ...plugin_settings import set_default_plugin_settings


class Command(BaseCommand):
    help = "Setup wjs_review settings."  # noqa

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force the setup of the settings.",
        )

    def handle(self, *args, **options):
        set_default_plugin_settings(force=options["force"])
