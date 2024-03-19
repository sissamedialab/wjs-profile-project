"""Default WJS settings.

For details on how to use this, see
https://gitlab.sissamedialab.it/wjs/specs/-/wikis/setup-janeway#set-settings
"""
from core.janeway_global_settings import TEMPLATES
from django.urls import reverse_lazy

INSTALLED_APPS = [
    "wjs",
    "wjs.jcom_profile",
    "easy_select2",
    "rosetta",
    "django_fsm",
    "model_utils",
    "django_bootstrap5",
    "hijack.contrib.admin",
    "django_filters",
]

# This is the default redirect if no other sites are found.
DEFAULT_HOST = "https://www.example.org"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "default@default.it"

LOGIN_REDIRECT_URL = reverse_lazy("core_edit_profile")
LOGIN_URL = reverse_lazy("core_login")

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
    ("en-us", ugettext("English (US)")),
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

MIDDLEWARE = ("hijack.middleware.HijackUserMiddleware",)
CORE_PRIVACY_MIDDLEWARE_ALLOWED_URLS = [
    "/profile/",
    "/logout/",
]

RESET_PASSWORD_SUBJECT = "Reset password"
RESET_PASSWORD_BODY = """Dear {} {}, please add your password to complete
the registration process before first login: click here {}
"""

WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    None: "wjs_review.events.assignment.default_assign_editors_to_articles",
    "JCOM": "wjs_review.events.assignment.jcom_assign_editors_to_articles",
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

# Http auth to access munin graphs (specs#486)
WJS_MUNIN_AUTH = ("username", "password")

ENABLE_FULL_TEXT_SEARCH = True
CORE_FILETEXT_MODEL = "core.PGFileText"
SUMMERNOTE_THEME = "bs5"

# Number of days after which an "unread" message is considered "late" and requiring attention.
WJS_UNREAD_MESSAGES_LATE_AFTER = 3
# Override to dev email address to test newsletter on actual email client
WJS_NEWSLETTER_TEST_RECIPIENT = ""

HIJACK_USERS_ENABLED = True
HIJACK_PERMISSION_CHECK = "wjs.jcom_profile.permissions.hijack_eo_and_admins_only"

# PROPHY SETTINGS
PROPHY_ORGANIZATION = "SISSA"

# prophy upload
PROPHY_API_KEY = ""
PROPHY_URL = "https://www.prophy.science/api/external/proposal/"

# JWT token
PROPHY_JWT_SUB = "SISSA"
PROPHY_JWT_URL = "https://www.prophy.science/api/auth/api-jwt-login/?token="
PROPHY_JWT_KEY = ""

# prophy author page
PROPHY_AUTH = "https://www.prophy.science/author/"

# How many days are considered "too far in the future" when postponing an EditorRevisionRequest
REVISION_REQUEST_DATE_DUE_MAX_THRESHOLD = 30
DAYS_CONSIDERED_FAR_FUTURE = 30

# refs #648
# https://gitlab.sissamedialab.it/wjs/specs/-/issues/648
# Default timedelta in days when the Editor sets the acceptance_due_date for the AssignToReviewer form
DEFAULT_ACCEPTANCE_DUE_DATE_DAYS = 7
# Min and max timedelta in days when the Editor sets the acceptance_due_date for the AssignToReviewer form
DEFAULT_ACCEPTANCE_DUE_DATE_MIN = 1
DEFAULT_ACCEPTANCE_DUE_DATE_MAX = 12

TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS = 3
