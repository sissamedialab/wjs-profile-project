"""Install all custom plugins into Janeway."""

import os
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Install custom plugins into Janeway."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        destination_folder = Path(settings.BASE_DIR) / "plugins"
        import wjs.plugins as me

        plugins_folder = Path(me.__file__).parent
        for plugin in plugins_folder.iterdir():
            if plugin.name.startswith("__"):
                continue

            destination = destination_folder / plugin.name

            self.stdout.write(self.style.SUCCESS(f"Linking {plugin.name} to {destination}..."))
            try:
                os.symlink(plugin.absolute(), destination)
                call_command("install_plugins", plugin.name)
            except FileExistsError:
                if destination.readlink() == plugin:
                    self.stdout.write(self.style.SUCCESS("...link to plugin already there, skipping link."))
                    self.stdout.write(self.style.SUCCESS("...running install plugins anyway."))
                    # even if the link is already there, we install the plugin anyway
                    call_command("install_plugins", plugin.name)
                else:
                    self.stderr.write(self.style.ERROR("...different file exists! Please check."))
                    self.stderr.write(self.style.ERROR(f"{plugin.name} VS {destination.readlink()}"))
            else:
                self.stdout.write(self.style.SUCCESS("...done."))
