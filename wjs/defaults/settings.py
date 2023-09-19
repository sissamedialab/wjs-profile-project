"""Default WJS settings.

For details on how to use this, see
https://gitlab.sissamedialab.it/wjs/specs/-/wikis/setup-janeway#set-settings
"""

from core.janeway_global_settings import TEMPLATES

INSTALLED_APPS = [
    "wjs",
    "wjs.jcom_profile",
    "easy_select2",
    "rosetta",
    "django_fsm",
    "model_utils",
    "django_bootstrap5",
]

# This is the default redirect if no other sites are found.
DEFAULT_HOST = "https://www.example.org"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "default@default.it"

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


def ugettext(s):
    """Let Django statically translate the verbose names of the languages using the standard i18n solution."""
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


URL_CONFIG = "domain"  # path or domain

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

# OIDC Settings
ENABLE_OIDC = False
OIDC_SERVICE_NAME = "OIDC Service Name"
OIDC_RP_CLIENT_ID = ""
OIDC_RP_CLIENT_SECRET = ""
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_OP_AUTHORIZATION_ENDPOINT = ""
OIDC_OP_TOKEN_ENDPOINT = ""
OIDC_OP_USER_ENDPOINT = ""
OIDC_OP_JWKS_ENDPOINT = ""

ENABLE_FULL_TEXT_SEARCH = False  # Read the docs before enabling full text

# Model used for indexing full text files
CORE_FILETEXT_MODEL = "core.FileText"  # Use "core.PGFileText" for Postgres

DEBUG = True

MIDDLEWARE = (
    # "wjs.jcom_profile.middleware.PrivacyAcknowledgedMiddleware",
)
CORE_PRIVACY_MIDDLEWARE_ALLOWED_URLS = [
    "/profile/",
    "/logout/",
]

RESET_PASSWORD_SUBJECT = "Reset password"
RESET_PASSWORD_BODY = """Dear {} {}, please add your password to complete
the registration process before first login: click here {}
"""

WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    None: "wjs.jcom_profile.events.assignment.default_assign_editors_to_articles",
    "JCOM": "wjs.jcom_profile.events.assignment.jcom_assign_editors_to_articles",
}

WJS_REVIEW_CHECK_FUNCTIONS = {
    None: ("wjs_review.events.checks.always_accept",),
    "JCOM": ("wjs_review.events.checks.always_accept",),
}

TEMPLATES[0]["OPTIONS"]["context_processors"].append("wjs.jcom_profile.context_processors.date_format")

INSTALLATION_BASE_THEME = "material"
SELECT2_USE_BUNDLED_JQUERY = False

TIME_ZONE = "Europe/Rome"

# SETTINGS_MODULE is used by rosetta to find the po files
SETTINGS_MODULE = "core.settings"

# Line-length of the edited PO file.
# Set this to 0 to mimic makemessage’s --no-wrap option.
# https://django-rosetta.readthedocs.io/settings.html
ROSETTA_POFILE_WRAP_WIDTH = 0

# Fall-backs if there is no date format specified for the active language
DATE_FORMAT = "M d, Y"
DATETIME_FORMAT = "M d, Y H:i:s"

DATE_FORMATS = {
    "en": "M d, Y",
    "es": "d b Y",
    "pt": "d b Y",
}
DATETIME_FORMATS = {
    "en": "M d, Y H:i:s",
    "es": "d b Y H:i:s",
    "pt": "d b Y H:i:s",
}


WJS_NEWSLETTER_TOKEN_SALT = "CHANGEME"


ENABLE_FULL_TEXT_SEARCH = True
CORE_FILETEXT_MODEL = "core.PGFileText"
SUMMERNOTE_THEME = "bs5"
