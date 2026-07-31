from pathlib import Path

from core.models import HomepageElement
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from journal.models import Journal
from utils import plugins

PLUGIN_NAME = "WJS Home blocks"
DISPLAY_NAME = "WJS Home blocks"
DESCRIPTION = "A plugin to provide home page blocks"
AUTHOR = "Nephila"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
JANEWAY_VERSION = "1.4.3"
MANAGER_URL = f"{SHORT_NAME}_manager"


class WJSHomePageBlocks(plugins.Plugin):
    short_name = SHORT_NAME
    plugin_name = PLUGIN_NAME
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    author = AUTHOR
    version = VERSION
    janeway_version = JANEWAY_VERSION
    is_workflow_plugin = False
    manager_url = MANAGER_URL

    @staticmethod
    def create_home_page_elements(journal):
        content_type = ContentType.objects.get_for_model(journal)
        elements = []
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME}",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_setup.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Block 1",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_home_block_1.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Block 2",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_home_block_2.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Block 3",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_home_block_3.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Block 4",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_home_block_4.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Personal Area",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_personal_area.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Login box",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_login_box.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        elements.append(
            HomepageElement.objects.get_or_create(
                name=f"{PLUGIN_NAME} - Start submission",
                content_type=content_type,
                object_id=journal.pk,
                defaults=dict(
                    template_path="homepage_elements/wjs_submit.html",
                    has_config=True,
                    configure_url=MANAGER_URL,
                ),
            )[0]
        )
        return elements


def install():
    """Register the plugin instance and create the corresponding HomepageElement."""
    WJSHomePageBlocks.install()
    journals = Journal.objects.all()
    for journal in journals:
        WJSHomePageBlocks.create_home_page_elements(journal)


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
    PluginConfig = apps.get_model("wjs_home_blocks.PluginConfig")

    element = PluginConfig.objects.filter(journal=request.journal).first()
    if not element:
        return {
            "main_column_items": 1,
            f"{SHORT_NAME}_element": element,
        }
    return {
        "main_column_items": element.main_column_items,
        f"{SHORT_NAME}_element": element,
    }
