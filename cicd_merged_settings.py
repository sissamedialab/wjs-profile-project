"""Merge janeway_global_settings and custom settings. Suitable for pytest."""
# isort: off
from .janeway_global_settings import *  # NOQA

# This file will be installed as core.cicd_merged_settings (a
# "sibling" to janeway_global_settings)
from .cicd_settings import *  # NOQA

from .janeway_global_settings import INSTALLED_APPS
from .janeway_global_settings import MIDDLEWARE_CLASSES as DEFAULT_MIDDLEWARE

from .cicd_settings import INSTALLED_APPS as CUSTOM_APPS
from .cicd_settings import MIDDLEWARE_CLASSES as CUSTOM_MIDDLEWARE

INSTALLED_APPS.extend(CUSTOM_APPS)
# MIDDLEWARE_CLASSES is a tuple, not a list
MIDDLEWARE_CLASSES = DEFAULT_MIDDLEWARE + CUSTOM_MIDDLEWARE

print("↣")
