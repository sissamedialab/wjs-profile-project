"""Merge janeway_global_settings and custom settings. Suitable for pytest."""
# isort: off
from .janeway_global_settings import *  # NOQA

# This file will be installed as core.cicd_merged_settings (a
# "sibling" to janeway_global_settings)
from .cicd_settings import *  # NOQA

from .janeway_global_settings import INSTALLED_APPS
from .janeway_global_settings import MIDDLEWARE_CLASSES as DEFAULT_MIDDLEWARE

from .cicd_settings import INSTALLED_APPS as CUSTOM_APPS
from .cicd_settings import MIDDLEWARE_CLASSES as CUSTOM_MIDDLEWARE
from collections.abc import Mapping

INSTALLED_APPS.extend(CUSTOM_APPS)
INSTALLED_APPS.extend(
    ["wjs.plugins.wjs_subscribe_newsletter", "wjs.plugins.wjs_latest_news", "wjs.plugins.wjs_latest_articles"],
)
# MIDDLEWARE_CLASSES is a tuple, not a list
MIDDLEWARE_CLASSES = DEFAULT_MIDDLEWARE + CUSTOM_MIDDLEWARE


def ugettext(s):
    """Let Django (statically) translate the verbose names of the languages using the standard i18n solution."""
    return s


LANGUAGES = (
    ("en", ugettext("English")),
    ("fr", ugettext("French")),
    ("de", ugettext("German")),
    ("nl", ugettext("Dutch")),
    ("cy", ugettext("Welsh")),
    ("es", ugettext("Spanish")),
    ("pt", ugettext("Portughese")),
)

MODELTRANSLATION_DEFAULT_LANGUAGE = "en"
MODELTRANSLATION_PREPOPULATE_LANGUAGE = "en"

MODELTRANSLATION_FALLBACK_LANGUAGES = {
    "default": ("en", "es", "pt"),
    "es": ("pt", "en"),
    "pt": ("es", "en"),
}

TIME_ZONE = "Europe/Rome"

# Ported class from janeway to skip migrations.
# Data created by migrations must be recreated by fixtures in conftest
IN_TEST_RUNNER = True


class SkipMigrations(Mapping):
    def __getitem__(self, key):
        """Ensure the install migrations run before syncing db.

        Django's migration executor will always pre_render database state from
        the models of unmigrated apps before running those declared in
        MIGRATION_MODULES. As a result, we can't run the install migrations
        first, while skipping the remaining migrations. Instead, we run
        the required SQL here.
        """
        if key == "install":
            from django.db import connection

            if connection.vendor == "postgresql":
                cursor = connection.cursor()
                cursor.execute("CREATE EXTENSION IF NOT EXISTS citext;")

        return None

    def __contains__(self, key):
        return True

    def __iter__(self):
        return iter("")

    def __len__(self):
        return 1


MIGRATION_MODULES = SkipMigrations()
