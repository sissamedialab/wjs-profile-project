"""Process any SCSS and copy the resulting files into the main static folder.

Or just pass if not required. See
path/to/janeway/src/themes/OLH/build_assets.py as an example.

https://janeway.readthedocs.io/en/latest/configuration.html#theming

"""

import os

from django.conf import settings
from journal import models as journal_models

# Cannot directly use themes.material.build_assets.process_journals
# because destination dir is hardcoded
from themes.material.build_assets import copy_file


def process_journals():
    """Copy css overrides to static/JCOM-theme folder."""
    journals = journal_models.Journal.objects.all()
    # TODO: rely on journal's base theme
    # Don't use a sub-theme: the base.html template has "material" hardcoded
    theme_name = "material"
    for journal in journals:
        for file in journal.scss_files:
            if file.endswith("material_override.css"):
                print("Copying material override file for {name}".format(name=journal.name))
                override_css_dir = os.path.join(settings.BASE_DIR, "static", theme_name, "css")
                override_css_file = os.path.join(override_css_dir, "journal{}_override.css".format(str(journal.id)))

                # test if the journal CSS directory exists and create it if not
                os.makedirs(override_css_dir, exist_ok=True)

                # copy file to static
                copy_file(file, override_css_file)


def process_admin():
    """Process css that override "admin" stuff.

    Temporary workaround waiting for proper configuration.
    """
    # similar to wjs.jcom_profile.management.commands.install_theme
    destination = os.path.realpath(os.path.join(settings.BASE_DIR, "static/admin/css/app.css"))
    import wjs.jcom_profile as me

    app_css_path = os.path.realpath(os.path.join(me.__file__, "../..", "themes/JCOM-theme/assets/app.css"))
    try:
        os.symlink(app_css_path, destination)
    except FileExistsError:
        if os.path.islink(destination) and os.readlink(destination) == app_css_path:
            print("...link to app.css already there, nothing to do.")
        else:
            print("...different file exists! Please check.")
            print(f"{app_css_path} VS {os.path.realpath(destination)}")
    else:
        print("...done.")


def build():
    """Build assets and copy them to static folder."""
    process_journals()
    process_admin()