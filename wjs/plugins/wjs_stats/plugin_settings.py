"""A plugin to collect some stats."""

from pathlib import Path
from typing import Any, Dict

from utils import plugins
from utils.logger import get_logger

logger = get_logger(__name__)

PLUGIN_NAME = "WJS Stats"
DISPLAY_NAME = "WJS Stats"
DESCRIPTION = "WJS Stats"
AUTHOR = "Medialab"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
MANAGER_URL = "wjs_stats_manager"
JANEWAY_VERSION = "1.5.0"

# The name of the group "Accounting"
GROUP_ACCOUNTING = "Accounting"


class WJSStats(plugins.Plugin):
    plugin_name = PLUGIN_NAME
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    author = AUTHOR
    short_name = SHORT_NAME
    manager_url = MANAGER_URL
    version = VERSION
    janeway_version = JANEWAY_VERSION


def install():
    """Register the plugin instance."""
    WJSStats.install()
    ensure_accounting_group_exists()


def hook_registry() -> Dict[str, Any]:
    """Register hooks for current plugin."""
    return {}


def ensure_accounting_group_exists():
    """Add a group called accounting.

    Users belonging to this group can access the DOI-count pages even if they are not staff.
    """
    from django.contrib.auth.models import Group

    group, created = Group.objects.get_or_create(name=GROUP_ACCOUNTING)
    if created:
        logger.info(f"Created group {group.name}.")
