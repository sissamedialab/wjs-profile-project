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

INSTALLED_APPS.extend(
    ["wjs.plugins.wjs_subscribe_newsletter", "wjs.plugins.wjs_latest_news", "wjs.plugins.wjs_latest_articles"],
)
