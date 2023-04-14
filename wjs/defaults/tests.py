"""Merge janeway_global_settings and custom settings for pytest."""
from core.janeway_global_settings import *
from .settings import *

from core.janeway_global_settings import INSTALLED_APPS
from core.janeway_global_settings import MIDDLEWARE_CLASSES as default_middleware
from .settings import INSTALLED_APPS as custom_apps
from .settings import MIDDLEWARE_CLASSES as custom_middleware
from collections.abc import Mapping

wjs_middleware = (
    "wjs.jcom_profile.middleware.PrivacyAcknowledgedMiddleware",
)

INSTALLED_APPS.extend(custom_apps)
# MIDDLEWARE_CLASSES is a tuple, not a list
MIDDLEWARE_CLASSES = default_middleware + custom_middleware + wjs_middleware

# NEWSLETTER_URL = "http://testserver.com"

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
