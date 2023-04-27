"""Sample setting for deployed environments."""
from wjs.defaults.settings import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "janeway",
        "USER": "postgres",
        "PASSWORD": "",
        "HOST": "",
        "PORT": "",
    },
}

WJAPP_JCOM_APIKEY = "..."
SECRET_KEY = "..."
