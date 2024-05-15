"""Process any SCSS and copy the resulting files into the main static folder.

Or just pass if not required. See
path/to/janeway/src/themes/OLH/build_assets.py as an example.

https://janeway.readthedocs.io/en/latest/configuration.html#theming

"""

import os
import shutil

import sass
from django.conf import settings
from django.core.management import call_command

BASE_THEME_DIR = os.path.join(settings.BASE_DIR, "static", "JCOM-theme")
SRC_THEME_DIR = os.path.dirname(__file__)
THEME_CSS_FILES = [
    os.path.join(BASE_THEME_DIR, "css", "jcom.css"),
    os.path.join(BASE_THEME_DIR, "css", "jcomal.css"),
    os.path.join(BASE_THEME_DIR, "css", "newsletter_jcom.css"),
    os.path.join(BASE_THEME_DIR, "css", "newsletter_jcomal.css"),
    os.path.join(BASE_THEME_DIR, "css", "newsletter_mobile.css"),
    os.path.join(BASE_THEME_DIR, "css", "wjs_review.css"),
]


def process_scss():
    """Compiles SCSS into CSS in the Static Assets folder."""
    include_path_materialize = os.path.join(
        SRC_THEME_DIR,
        "assets",
        "materialize-src",
        "sass",
    )
    include_path_bootstrap = os.path.join(
        SRC_THEME_DIR,
        "assets",
    )

    for css_file in THEME_CSS_FILES:
        app_scss_file = os.path.join(
            SRC_THEME_DIR,
            "assets",
            "sass",
            f"{os.path.splitext(os.path.basename(css_file))[0]}.scss",
        )

        include_path_jcom = os.path.dirname(app_scss_file)
        compiled_css_from_file = sass.compile(
            filename=app_scss_file,
            include_paths=[include_path_jcom, include_path_materialize, include_path_bootstrap],
        )

        # Open the CSS file and write into it
        with open(css_file, "w", encoding="utf-8") as write_file:
            write_file.write(compiled_css_from_file)


def create_paths():
    """Create destination dirs for css & co."""
    folders = [
        "css",
        "js",
        "fonts",
    ]

    for folder in folders:
        os.makedirs(os.path.join(BASE_THEME_DIR, folder), exist_ok=True)
    return os.path.join(BASE_THEME_DIR, "css")


def build():
    """Build assets and copy them to static folder."""
    print("JCOM SCSS START")
    create_paths()
    print("JCOM PATHS DONE")
    process_scss()
    print("JCOM SCSS DONE")
    copy_file("themes/JCOM-theme/assets/materialize-src/fonts", "static/JCOM-theme/fonts", False)
    copy_file(
        "themes/JCOM-theme/assets/materialize-src/js/bin/materialize.min.js",
        "static/JCOM-theme/js/materialize.min.js",
    )
    call_command("collectstatic", "--noinput")
    print("JCOM collectstatic DONE")


def copy_file(source, destination, is_file=True):
    """
    :param source: The source of the folder for copying
    :param destination: The destination folder for the file
    :return:
    """

    destination_folder = os.path.join(settings.BASE_DIR, os.path.dirname(destination))

    if is_file:
        if not os.path.exists(destination_folder):
            os.makedirs(destination_folder, exist_ok=True)
        shutil.copy(os.path.join(settings.BASE_DIR, source), os.path.join(settings.BASE_DIR, destination))
    else:
        shutil.copytree(
            os.path.join(settings.BASE_DIR, source),
            os.path.join(settings.BASE_DIR, destination),
            dirs_exist_ok=True,
        )
