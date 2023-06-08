"""
Merge janeway_global_settings and custom settings for pytest.

isort:skip_file
"""
from collections.abc import Mapping  # noqa

from core.janeway_global_settings import *  # noqa
from .settings import *  # noqa

try:
    from core.settings import *  # noqa
except ImportError:
    # Non committed local settings may non exists (eg: in the CI)
    pass


from core.janeway_global_settings import INSTALLED_APPS, MIDDLEWARE as DEFAULT_MIDDLEWARE  # noqa

from .settings import INSTALLED_APPS as CUSTOM_APPS, MIDDLEWARE as CUSTOM_MIDDLEWARE

NEWSLETTER_URL = "https://jcom.sissa.it"

WJS_MIDDLEWARE = ("wjs.jcom_profile.middleware.PrivacyAcknowledgedMiddleware",)

INSTALLED_APPS.extend(CUSTOM_APPS)
MIDDLEWARE = DEFAULT_MIDDLEWARE + CUSTOM_MIDDLEWARE + WJS_MIDDLEWARE  # noqa

# NEWSLETTER_URL = "http://testserver.com"  # noqa

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
