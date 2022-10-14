"""Merge janeway_global_settings and custom settings for pytest."""

import os

from core.janeway_global_settings import *

# Install my stuff
INSTALLED_APPS.extend(
    [
        "wjs.jcom_profile",
    ]
)

MIDDLEWARE_CLASSES += (
    "wjs.jcom_profile.middleware.PrivacyAcknowledgedMiddleware",
)

# SECURITY WARNING: keep the secret key used in production secret!
# You should change this key before you go live!
SECRET_KEY = "uxprsdhk^gzd-r=_287byolxn)$k6tsd8_cepl^s^tms2w1qrv"

# This is the default redirect if no other sites are found.
DEFAULT_HOST = "https://janeway.sissamedialab.it"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
LOGIN_REDIRECT_URL = "/user/profile/"

# CATCHA_TYPE should be either 'simple_math', 'recaptcha' or 'hcaptcha' to enable captcha
# fields, otherwise disabled
CAPTCHA_TYPE = "simple_math"

# If using recaptcha complete the following
RECAPTCHA_PRIVATE_KEY = ""
RECAPTCHA_PUBLIC_KEY = ""

# If using hcaptcha complete the following:
HCAPTCHA_SITEKEY = ""
HCAPTCHA_SECRET = ""

# ORCID Settings
ENABLE_ORCID = True
ORCID_API_URL = "http://pub.orcid.org/v1.2_rc7/"
ORCID_URL = "https://orcid.org/oauth/authorize"
ORCID_TOKEN_URL = "https://pub.orcid.org/oauth/token"
ORCID_CLIENT_SECRET = ""
ORCID_CLIENT_ID = ""

# Default Langague
LANGUAGE_CODE = "en"

# Probably not used!!! TODO: remove (mg)
URL_CONFIG = "path"  # path or domain

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "janeway",
        "USER": "janeway",
        "PASSWORD": "janeway",
        "HOST": "db",
        "PORT": "5432",
    }
}

# Set DEBUG to True or static assets are not served by `runserver`
DEBUG = True

# Use mailcatcher for emails
# https://docs.djangoproject.com/en/1.11/topics/email/
EMAIL_HOST = "localhost"
EMAIL_PORT = 1025
EMAIL_USE_TLS = False


INTERNAL_IPS = [
    # ...
    "127.0.0.1",
    # ...
]

USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True


# already defined in janeway_global_settings.py (but not visible here...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {
        "level": "DEBUG" if DEBUG else "INFO",
        "handlers": ["console", "log_file"],
    },
    "formatters": {
        "default": {
            "format": "%(levelname)s %(asctime)s %(module)s "
            "P:%(process)d T:%(thread)d %(message)s",
        },
        "coloured": {
            "()": "colorlog.ColoredFormatter",
            "format": "%(log_color)s%(levelname)s %(asctime)s M:%(module)s: "
            "%(message)s",
            # 'P:%(process)d T:%(thread)d %(message)s',
            "log_colors": {
                "DEBUG": "cyan",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "coloured",
            "stream": "ext://sys.stdout",
        },
        "log_file": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "maxBytes": 1024 * 1024 * 50,  # 50 MB
            "backupCount": 1,
            "filename": os.path.join(BASE_DIR, "../logs/janeway.log"),
            "formatter": "default",
        },
    },
    # to ge the logger names, add "%(name)s" to the formatter
    "loggers": {
        "django.db.backends": {
            "level": "WARNING",
            "handlers": ["console", "log_file"],
            "propagate": False,
        },
        "parso.python.diff": {
            "level": "WARNING",
        },
        "parso.cache": {
            "level": "WARNING",
        },
        "asyncio": {
            "level": "WARNING",
        },
        "core.include_urls": {
            "level": "WARNING",
        },
    },
}

ENABLE_FULL_TEXT_SEARCH = False  # Read the docs before enabling full text

# Model used for indexing full text files
CORE_FILETEXT_MODEL = "core.PGFileText"  # Use "core.PGFileText" for Postgres

# Invite email settings
# https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/3
JOIN_JOURNAL_SUBJECT = "Join journal"
JOIN_JOURNAL_BODY = "Dear {} {},\n{}; to continue click the following link:{}"

RESET_PASSWORD_SUBJECT = "Reset password"
RESET_PASSWORD_BODY = """Dear {} {}, please add your password to complete
the registration process before first login: click here {}
"""

print("🍠")
