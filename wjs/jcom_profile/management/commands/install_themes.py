"""Install all custom themes into Janeway."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Install custom themes into Janeway."  # NOQA

    def handle(self, *args, **options):
        """Command entry point."""
        destination_folder = os.path.realpath(os.path.join(settings.BASE_DIR, "themes"))
        import wjs.jcom_profile as me

        themes_folder = os.path.realpath(os.path.join(me.__file__, "../..", "themes"))
        for theme in os.listdir(themes_folder):
            destination = os.path.join(destination_folder, theme)
            self.stdout.write(self.style.NOTICE(f"Linking {theme} to {destination}..."))
            theme_folder = os.path.join(themes_folder, theme)
            try:
                os.symlink(theme_folder, destination)
            except FileExistsError:
                if os.readlink(destination) == theme_folder:
                    self.stdout.write(self.style.NOTICE("...link to theme already there, nothing to do."))
                else:
                    self.stderr.write(self.style.ERROR("...different file exists! Please check."))
                    self.stderr.write(self.style.ERROR(f"{theme_folder} VS {os.readlink(destination)}"))
            else:
                self.stdout.write(self.style.NOTICE("...done."))
