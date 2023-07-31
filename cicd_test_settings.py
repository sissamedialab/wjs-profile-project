"""
CI/CD settings to run tests in CI/CD.

As the "janeway way" of handling settings and initializing the project is not triggered by pytest,
we must configure the project as normal django project, because we don't have janeway automation tools available.
"""
from wjs.defaults.tests import *  # noqa

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
