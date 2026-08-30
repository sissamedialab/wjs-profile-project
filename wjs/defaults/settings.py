"""
Default WJS settings.

For details on how to use this, see
https://gitlab.sissamedialab.it/wjs/specs/-/wikis/setup-janeway#set-settings
"""

import os
from pathlib import Path

from core.janeway_global_settings import STATIC_URL, TEMPLATES
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

INSTALLED_APPS = [
    "wjs.jcom_profile",
    "easy_select2",
    "rosetta",
    "django_fsm",
    "model_utils",
    "django_bootstrap5",
    "hijack.contrib.admin",
    "django_filters",
    "django_q",
    "wjs.themes",
    "wjs.advanced_admin",
    "rest_framework.authtoken",
]

try:
    import wjs_mgmt_cmds

    INSTALLED_APPS.append(
        "wjs_mgmt_cmds",
    )
except ImportError:
    pass

try:
    import wjs.user_search

    INSTALLED_APPS.append(
        "wjs.user_search",
    )
except ImportError:
    pass

REDIS_CACHE_URL = os.environ.get("REDIS_CACHE_URL", "redis://localhost:6379/1")
REDIS_QCLUSTER_URL = os.environ.get("REDIS_QCLUSTER_URL", "redis://localhost:6379/10")
# TODO: parametrize CHANNEL_LAYERS redis CONFIG?

ASGI_APPLICATION = "wjs.channels.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}

# This is the default redirect if no other sites are found.
DEFAULT_HOST = "https://www.example.org"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "default@default.it"

LOGIN_REDIRECT_URL = reverse_lazy("core_edit_profile")
LOGIN_URL = "/login/"

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
ENABLE_ORCID = False
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

MIDDLEWARE = ()
CORE_PRIVACY_MIDDLEWARE_ALLOWED_URLS = [
    "/profile/",
    "/logout/",
]

RESET_PASSWORD_SUBJECT = "Reset password"  # noqa: S105
RESET_PASSWORD_BODY = """Dear {} {}, please add your password to complete
the registration process before first login: click here {}
"""  # noqa: S105

# Functions that check if a just-submitted article might have issues
# that would require EO attention before editor assigment
WJS_REVIEW_CHECK_FUNCTIONS = {
    None: ("plugins.wjs_review.events.checks.always_accept",),
    "JCOM": ("plugins.wjs_review.events.checks.always_accept",),
}

# Functions that determine which editor is assigned to an article
WJS_ARTICLE_ASSIGNMENT_FUNCTIONS = {
    None: "plugins.wjs_review.events.assignment.default_assign_editors_to_articles",
    "JCOM": "plugins.wjs_review.events.assignment.jcom_assign_editors_to_articles",
    "JCOMAL": "plugins.wjs_review.events.assignment.jcom_assign_editors_to_articles",
}

WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS = {
    None: "plugins.wjs_review.events.assignment.get_select_eo_by_workload",
}

# Functions that check if a just-accepted article might have issues
# that would prevent a typesetter from taking it in charge
WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS = {
    None: ("plugins.wjs_review.events.checks_after_acceptance.always_pass",),
    "JCOM": ("plugins.wjs_review.events.checks_after_acceptance.always_pass",),
    "JCOMAL": ("plugins.wjs_review.events.checks_after_acceptance.always_pass",),
    "JCAP": ("plugins.wjs_review.events.checks_after_acceptance.jcap_ta_not_yet_confirmed",),
}

# Email addresses that must receive the notification when an article is published.
# https://gitlab.sissamedialab.it/wjs/specs/-/issues/1705
WJS_ARTICLE_PUBLISHED_SOCIAL_NOTIFICATION_EMAILS = {
    None: ("j-social@medialab.sissa.it",),
    "JCOM": ("j-social@medialab.sissa.it",),
    "JCOMAL": ("j-social@medialab.sissa.it",),
}

# Press email addresses that must receive the notification when an article is published.
# https://gitlab.sissamedialab.it/wjs/specs/-/issues/1705
WJS_ARTICLE_WITHDRAWN_PRESS_NOTIFICATION_EMAILS = {
    None: ("j_journals_press@medialab.sissa.it",),
    "JCOM": ("j_journals_press@medialab.sissa.it",),
    "JCOMAL": ("j_journals_press@medialab.sissa.it",),
}
# Press email enabled flag
WJS_ARTICLE_WITHDRAWN_PRESS_NOTIFICATION_ENABLED = {
    None: False,
    "JCOM": True,
    "JCOMAL": False,
}

TEMPLATES[0]["OPTIONS"]["context_processors"].append("wjs.jcom_profile.context_processors.date_format")
TEMPLATES[0]["OPTIONS"]["context_processors"].append("wjs.themes.context_processors.wjs_themes_version")
# TODO: drop this when going to production
# (ATM wjs_review plugin is installed only in development instances; see specs#1132)
try:
    import plugins.wjs_review
except ImportError:
    pass
else:
    TEMPLATES[0]["OPTIONS"]["context_processors"].append("plugins.wjs_review.context_processors.visibility_flags")

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
DATETIME_FORMAT_NO_SECONDS = "M d, Y H:i"
TIME_FORMAT_NO_SECONDS = "H:i"
DATE_FORMAT_STRFTIME = "%d %M"

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


# MariaDB connection to import data from wjapp
# (one dictionary for each journal: WJAPP_JCOM_... WJAPP_JCOMAL_... etc.)
WJAPP_JCOM_IMPORT_CONNECTION_PARAMS = {
    "user": "",
    "password": "",
    "host": "",
    "database": "",
}
WJAPP_JCOMAL_IMPORT_CONNECTION_PARAMS = {
    "user": "",
    "password": "",
    "host": "",
    "database": "",
}
WJAPP_JCAP_IMPORT_CONNECTION_PARAMS = {
    "user": "",
    "password": "",
    "host": "",
    "database": "",
}

# http wjapp login data to import files from wjapp
# (one dictionary for each journal: WJAPP_JCOM_... WJAPP_JCOMAL_... etc.)
WJAPP_JCOM_IMPORT_LOGIN_PARAMS = {
    "username": "",
    "password": "",
    "login_base_url": "",
    "http_ba_username": "",
    "http_ba_password": "",
}
WJAPP_JCOMAL_IMPORT_LOGIN_PARAMS = {
    "username": "",
    "password": "",
    "login_base_url": "",
    "http_ba_username": "",
    "http_ba_password": "",
}

# base url for files import from wjapp (one for journal)
WJAPP_JCOM_BASE_URL = "https://old.wjapp.it/jcom/common/archiveFile?filePath="
WJAPP_JCOMAL_BASE_URL = "https://old.wjapp.it/jcomal/common/archiveFile?filePath="


# JCAP settings for import files from the filesystem
WJAPP_JCAP_IMPORT_ARCHIVE_CURRENT = ""
WJAPP_JCAP_IMPORT_ARCHIVE_OLD = ""
WJAPP_JCAP_IMPORT_ARCHIVE_CURRENT_DEDUP = ""
WJAPP_JCAP_IMPORT_ARCHIVE_OLD_DEDUP = ""
WJAPP_JCAP_IMPORT_DEDUP_SCRIPT = ""

NO_NOTIFICATION = False

ENABLE_FULL_TEXT_SEARCH = True
CORE_FILETEXT_MODEL = "core.PGFileText"

# Override to dev email address to test newsletter on actual email client
WJS_NEWSLETTER_TEST_RECIPIENT = ""

HIJACK_USERS_ENABLED = True
HIJACK_PERMISSION_CHECK = "wjs.jcom_profile.permissions.hijack_eo_and_admins_only"

# PROPHY SETTINGS
PROPHY_ORGANIZATION = "SISSA"

# prophy upload
PROPHY_API_KEY = ""
PROPHY_URL = "https://www.prophy.ai/api/external/proposal/"

# JWT token
PROPHY_JWT_SUB = "SISSA"
PROPHY_JWT_URL = "https://www.prophy.ai/api/auth/api-jwt-login/?token="
PROPHY_JWT_KEY = ""

# prophy author page
PROPHY_AUTH = "https://www.prophy.ai/author/"

# How many days are considered "too far in the future" when postponing a review request
REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD = 30

# Refs. specs #648 (#1159) #1158
# Default timedelta in days when the Editor sets the "acceptance" due-date for review-assignment invitation: see journal setting default_review_acceptance_days
#
# Min and max timedelta in days when the Editor sets the acceptance_due_date for the AssignToReviewer form
DEFAULT_ACCEPTANCE_DUE_DATE_MIN = 1
DEFAULT_ACCEPTANCE_DUE_DATE_MAX = 12

# refs #584
DEFAULT_REVIEW_DUE_DATE_DAYS = 21
DEFAULT_REVIEW_DUE_DATE_MIN = 0
DEFAULT_REVIEW_DUE_DATE_MAX = 28


TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS = 3

# When the last reminder has been sent (e.g. REVIEWER_SHOULD_WRITE_REVIEW_2) and the following number of days
# have passed, a reviewer (for instance) is considered "late". This can effect the "attention conditions".
WJS_REMINDER_LATE_AFTER = 3

Q_CLUSTER = {
    "name": "wjs-janeway",
    "label": "Task WJS",
    "workers": 1,
    "sync": True,
    "redis": REDIS_QCLUSTER_URL,
    "retry": 330,
    "timeout": 300,
}

# NB: do not change the cache backend:
# we use `django_cache.delete_pattern()` that only works with Redis!
# See
# - specs#2416
# - https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/1270#note_60214
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    },
}

# Seconds to keep role-for-article entries in Redis cache (15 days).
ROLE_FOR_ARTICLE_CACHE_TTL = 1296000
# Cache key version for role-for-article Redis entries.
ROLE_FOR_ARTICLE_CACHE_VER = 1

LOCALE_PATHS = [Path(__file__).parents[1] / "locale"]

PROOFING_ASSIGNMENT_MIN_DUE_DAYS = 3
PROOFING_ASSIGNMENT_MAX_DUE_DAYS = 7

JCOMASSISTANT_URL = "http://wjs-services.ud.sissamedialab.it:1234/api/v2/"
YAKUNIN_URL = "http://wjs-services.ud.sissamedialab.it:1235/"

# Extra configuration to be added to the .ini file sent to yakunin
# for the compilation of submitted files.
# See also kwargs used in yakunin.archive.
YAKUNIN_CONFIG = """
timeout_compilation = 131
"""

# Useful in development: set this to the path of a file that mimics what Jcomassistant would generate.
# See TypesetterTestsGalleyGeneration._mock_jcom_assistant_client()
JCOMASSISTANT_MOCK_FILE = ""

# Useful in development: set this to the path of a file mimicking files for typesetting uploaded by the author.
WJS_TYPESET_REVISION_MOCK_FILE = ""

# Override the default bootstrap5 css as we customize it, and the css below will include all the bootstrap5 css plus
# our own customizations
# We might have an issue if we want to customize this per journal, but I would leave as an issue as it has a low impact
# for now as it's just the dashboard css
BOOTSTRAP5 = {
    "css_url": "/static/wjs-bootstrap/css/base.css",
    "hyphenate_attribute_prefixes": ["data", "hx", "aria"],
}

# The list of journals that supports multiple languages and needs base english for display on the website
WJS_JOURNALS_WITH_ENGLISH_CONTENT = ["JCOMAL"]

# The list of journals for which the "send short description and image for social media" feature is enabled
WJS_JOURNALS_WITH_SOCIAL_MEDIA_FILES = ["JCOM", "JCOMAL"]

# The list of journals for which we should show editor keywords when selecting a new editor
WJS_SHOW_EDITOR_KEYWORDS = []

# Associating each journal with Its custom Review Form
WJS_REVIEW_CUSTOM_REPORT_FORMS = {
    None: "plugins.wjs_review.forms.JCOMReportForm",
    "JCOM": "plugins.wjs_review.forms.JCOMReportForm",
    "JCOMAL": "plugins.wjs_review.forms.JCOMReportForm",
    "JQuant": "plugins.wjs_review.forms.JQuantReportForm",
}

# (x,y) position of the watermark
WATERMARK_X_POSITION = 10
WATERMARK_Y_POSITION = 720

CORE_THEMES = [
    "OLH",
    "material",
    "clean",
    "wjs-bootstrap",
]

WJS_ARTICLE_LANGUAGES = {
    None: [("eng", _("English"))],
    "JCOM": [
        (
            "eng",
            _("English"),
        ),
        ("deu", _("German")),
        ("fra", _("French")),
        ("spa", _("Spanish")),
        ("por", _("Portuguese")),
        ("ita", _("Italian")),
    ],
    "JCOMAL": [("spa", _("Spanish")), ("por", _("Portuguese"))],
}

WJS_ARTICLE_KEYWORDS_LIMITS = {
    None: {
        "min": 1,
        "max": 3,
    },
}

WJS_ALLOW_DIRECTOR_HIJACKING = False
"""
Allow directors to hijack other users.
"""

WJS_ALLOW_HIJACK_SU_ACCOUNTS = True
"""
Allow EO to hijack superusers.
"""

WJS_USE_WJS_SUBMISSION = {
    None: True,
    "JCOM": True,
    "JCOMAL": True,
}
"""
Use custom submission/revision process or the standard one.
"""

ISSUE_TRACKER_URLS = {
    "wjs-help": "https://gitlab.sissamedialab.it/wjs/wjs-help/-/issues/",
    "rogne": "https://gitlab.sissamedialab.it/calderan/rogne/-/issues/",
    "post-production": "https://gitlab.sissamedialab.it/calderan/pipicor/-/issues/",
}
"""
URLs of the issue trackers used for the "Open Issue" button in actions section.
"""

PROFILE_FIELDS = {
    None: (
        "first_name",
        "middle_name",
        "last_name",
        "year_of_birth",
        "email",
        "gender",
        "profession",
        "biography",
        "alternative_email",
        "personal_interest",
        "publication_alert",
        "records_arxiv",
        "records_inspire",
        "records_scix",
        "facebook",
        "twitter",
        "linkedin",
        "records_other",
    ),
    "JCAP": (
        "first_name",
        "middle_name",
        "last_name",
        "year_of_birth",
        "email",
        "gender",
        "career_stage",
        "alternative_email",
        "personal_interest",
        "records_arxiv",
        "records_inspire",
        "records_scix",
        "records_other",
    ),
    "JCOM": (
        "first_name",
        "middle_name",
        "last_name",
        "year_of_birth",
        "email",
        "gender",
        "profession",
        "biography",
        "alternative_email",
        "personal_interest",
        "publication_alert",
        "facebook",
        "twitter",
        "linkedin",
        "records_other",
    ),
    "JCOMAL": (
        "first_name",
        "middle_name",
        "last_name",
        "year_of_birth",
        "email",
        "gender",
        "profession",
        "biography",
        "alternative_email",
        "personal_interest",
        "publication_alert",
        "facebook",
        "twitter",
        "linkedin",
        "records_other",
    ),
    "JQuant": (
        "first_name",
        "middle_name",
        "last_name",
        "year_of_birth",
        "email",
        "gender",
        "alternative_email",
        "personal_interest",
        "records_arxiv",
        "records_inspire",
        "records_other",
    ),
}

TINYMCE_JS_URL = f"{STATIC_URL}/tinymce/tinymce.min.js"


SUBMISSION_ARTICLE_LANGUAGES = WJS_ARTICLE_LANGUAGES
SUBMISSION_ENABLE_FREE_KEYWORD = False

SUBMISSION_UNIQUENESS_CHECK = {
    None: "plugins.wjs_review.unique_check.check_article_uniqueness_by_submission_status_and_section_in_all_journals_combined",  # noqa: ERA001, E501
}
