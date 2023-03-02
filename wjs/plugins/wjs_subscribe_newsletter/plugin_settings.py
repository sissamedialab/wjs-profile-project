from pathlib import Path

from comms.models import NewsItem
from core.models import HomepageElement
from django.contrib.contenttypes.models import ContentType
from journal.models import Journal
from utils import plugins

PLUGIN_NAME = "WJS Publication alert subscription"
DISPLAY_NAME = "WJS Publication alert subscription"
DESCRIPTION = "A plugin to provide Publication alert subscription form"
AUTHOR = "Nephila"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
JANEWAY_VERSION = "1.4.3"
MANAGER_URL = f"{SHORT_NAME}_manager"


class WJSLatestArticles(plugins.Plugin):
    short_name = SHORT_NAME
    plugin_name = PLUGIN_NAME
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    author = AUTHOR
    version = VERSION
    janeway_version = JANEWAY_VERSION
    is_workflow_plugin = False
    manager_url = MANAGER_URL


def install():
    """Register the plugin instance and create the corresponding HomepageElement."""
    WJSLatestArticles.install()
    journal = Journal.objects.first()
    content_type = ContentType.objects.get_for_model(journal)
    HomepageElement.objects.get_or_create(
        name=PLUGIN_NAME,
        defaults=dict(
            template_path="homepage_elements/wjs_publication_form.html",
            content_type=content_type,
            object_id=journal.pk,
            has_config=True,
            configure_url=MANAGER_URL,
        ),
    )


def hook_registry():
    """
    Register hooks for current plugin.

    Currently supported hooks:
    - yield_homepage_element_context
    """
    return {
        "yield_homepage_element_context": {
            "module": f"plugins.{SHORT_NAME}.plugin_settings",
            "function": "get_plugin_context",
            "name": PLUGIN_NAME,
        },
    }


def get_plugin_context(request, homepage_elements):
    from .models import PluginConfig

    element = PluginConfig.objects.filter(journal=request.journal).first()
    return {
        f"{SHORT_NAME}_element": element,
    }
