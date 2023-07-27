from pathlib import Path
from typing import Any, Dict

from core.models import SettingGroup
from django.utils.translation import gettext_lazy as _
from utils import plugins

from wjs.jcom_profile.custom_settings_utils import (
    SettingParams,
    SettingValueParams,
    create_customization_setting,
    get_group,
)

PLUGIN_NAME = "WJS Review articles"
DISPLAY_NAME = "WJS Review articles"
DESCRIPTION = "A plugin to provide WJS style review process"
AUTHOR = "Nephila"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
JANEWAY_VERSION = "1.5.0"
MANAGER_URL = f"{SHORT_NAME}_manager"

IS_WORKFLOW_PLUGIN = True
JUMP_URL = f"{SHORT_NAME}_article"
HANDSHAKE_URL = f"{SHORT_NAME}_articles"
ARTICLE_PK_IN_HANDSHAKE_URL = True
STAGE = f"{SHORT_NAME}_plugin"
KANBAN_CARD = "wjs_review/elements/card.html"
DASHBOARD_TEMPLATE = "wjs_review/elements/dashboard.html"


class WJSReviewArticles(plugins.Plugin):
    short_name = SHORT_NAME
    plugin_name = PLUGIN_NAME
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    author = AUTHOR
    version = VERSION
    janeway_version = JANEWAY_VERSION
    stage = STAGE
    manager_url = MANAGER_URL

    is_workflow_plugin = IS_WORKFLOW_PLUGIN
    handshake_url = HANDSHAKE_URL
    article_pk_in_handshake_url = ARTICLE_PK_IN_HANDSHAKE_URL


def install():
    """Register the plugin instance and create the corresponding HomepageElement."""
    WJSReviewArticles.install()
    set_default_plugin_settings()


def hook_registry() -> Dict[str, Any]:
    """
    Register hooks for current plugin.

    Currently supported hooks:
    - yield_homepage_element_context
    """
    return {}


def set_default_plugin_settings():
    """Create default settings for the plugin."""
    group_name = "wjs_review"
    try:
        wjs_review_settings_group = get_group(group_name)
    except SettingGroup.DoesNotExist:
        wjs_review_settings_group = SettingGroup.objects.create(name=group_name)

    def acceptance_due_date():
        acceptance_days_setting: SettingParams = {
            "name": "acceptance_due_date_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default acceptance due date timeframe"),
            "description": _(
                "Default number of days from current date to set acceptance_due_date.",
            ),
            "is_translatable": False,
        }
        acceptance_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 7,
            "translations": {},
        }
        create_customization_setting(
            acceptance_days_setting, acceptance_days_setting_value, acceptance_days_setting["name"]
        )

    def review_lists_page_size():
        review_lists_page_size_setting: SettingParams = {
            "name": "review_lists_page_size",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Page size of the items list"),
            "description": _(
                "Number of items in the lists / tables of items.",
            ),
            "is_translatable": False,
        }
        review_lists_page_size_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 40,
            "translations": {},
        }
        create_customization_setting(
            review_lists_page_size_setting,
            review_lists_page_size_setting_value,
            review_lists_page_size_setting["name"],
        )

    def review_invitation_message():
        review_invitation_message_setting: SettingParams = {
            "name": "review_invitation_message",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Default message for review invitation"),
            "description": _(
                "Provide the default message to invite reviewers.",
            ),
            "is_translatable": False,
        }
        review_invitation_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Please review the article"),
            "translations": {},
        }
        create_customization_setting(
            review_invitation_message_setting,
            review_invitation_message_setting_value,
            review_invitation_message_setting["name"],
        )

    acceptance_due_date()
    review_lists_page_size()
    review_invitation_message()
