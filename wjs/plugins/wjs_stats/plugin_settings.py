"""A plugin to collect some stats."""

from pathlib import Path
from typing import Any, Dict

from utils import plugins

PLUGIN_NAME = "WJS Stats"
DISPLAY_NAME = "WJS Stats"
DESCRIPTION = "WJS Stats"
AUTHOR = "Medialab"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
MANAGER_URL = "wjs_stats_manager"
JANEWAY_VERSION = "1.5.0"


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


def hook_registry() -> Dict[str, Any]:
    """Register hooks for current plugin."""
    return {}
