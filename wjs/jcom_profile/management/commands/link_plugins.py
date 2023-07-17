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

            if plugin.name == "wjs_review":
                # This plugin is not yet ready for production, but
                # it's "innocuous" so here we just don't install it if
                # the DEBUG setting is False, in order not to load
                # useless code.
                #
                # Warning: this introduces a difference between the
                # production and pre-production instances!
                if getattr(settings, "DEBUG", False) is False:
                    self.stderr.write(self.style.NOTICE(f"Refusing to install {plugin.name} when DEBUG=False."))
                    if destination.exists():
                        self.stderr.write(
                            self.style.ERROR(f"{plugin.name} seems installed but DEBUG=False. Please check!"),
                        )
                    continue

            self.stdout.write(self.style.SUCCESS(f"Linking {plugin.name} to {destination}..."))
            try:
                os.symlink(plugin.absolute(), destination)
                call_command("install_plugins", plugin.name)
            except FileExistsError:
                if destination.readlink() == plugin:
                    self.stdout.write(self.style.SUCCESS("...link to plugin already there, nothing to do."))
                else:
                    self.stderr.write(self.style.ERROR("...different file exists! Please check."))
                    self.stderr.write(self.style.ERROR(f"{plugin.name} VS {destination.readlink()}"))
            else:
                self.stdout.write(self.style.SUCCESS("...done."))
