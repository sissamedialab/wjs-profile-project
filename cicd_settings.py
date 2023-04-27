"""CI/CD settings to run management commands in CI/CD."""
from wjs.defaults.settings import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "janeway",
        "USER": "janeway",
        "PASSWORD": "janeway",
        "HOST": "db",
        "PORT": "5432",
    },
}
