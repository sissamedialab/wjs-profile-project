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
    patch_setting,
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
HANDSHAKE_URL = f"{SHORT_NAME}_list"
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
    try:
        wjs_review_settings_group = get_group("wjs_review")
    except SettingGroup.DoesNotExist:
        wjs_review_settings_group = SettingGroup.objects.create(name="wjs_review", enabled=True)
    email_settings_group = get_group("email")

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

    def declined_review_message():
        declined_review_message_setting: SettingParams = {
            "name": "declined_review_message",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Message shown when reviewer declines the review"),
            "description": _(
                "Provide a thank you message when reviewer declines the review.",
            ),
            "is_translatable": False,
        }
        declined_review_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Thanks for the time to evaluate the review."),
            "translations": {},
        }
        create_customization_setting(
            declined_review_message_setting,
            declined_review_message_setting_value,
            declined_review_message_setting["name"],
        )

    def do_review_message():
        do_review_message_setting: SettingParams = {
            "name": "do_review_message",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Message shown on review submit page"),
            "description": _(
                "Provide instructions to handle reviews.",
            ),
            "is_translatable": False,
        }
        do_review_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("To submit the review do this and this."),
            "translations": {},
        }
        create_customization_setting(
            do_review_message_setting,
            do_review_message_setting_value,
            do_review_message_setting["name"],
        )

    def patch_review_message():
        review_message_email_setting: SettingParams = {
            "name": "review_assignment",
            "group": email_settings_group,
            "types": "rich-text",
            "pretty_name": _("Message shown on review submit page"),
            "description": _(
                "Provide instructions to handle reviews.",
            ),
            "is_translatable": False,
        }
        review_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            {% load fqdn %}
            Dear {{ review_assignment.reviewer.full_name }},<br/><br/>
            {% if review_assignment.reviewer.jcomprofile.invitation_token %}
            You have been invited to {{ article.journal.name }} in order to review "{{ article.title }}".
            {% else %}
            We are requesting that you undertake a review of "{{ article.title }}" in {{ article.journal.name }}.
            {% endif %}
            <br/><br/>
            We would be most grateful for your time as the feedback from our reviewers is of the utmost importance
            to our editorial decision-making processes.<br/><br/>You can let us know your decision or decline to
            undertake the review:
            {% if review_assignment.reviewer.jcomprofile.invitation_token %}
                {% journal_base_url article.journal %}{% url 'wjs_evaluate_review' assignment_id=review_assignment.id token=review_assignment.reviewer.jcomprofile.invitation_token %}?access_code={{ review_assignment.access_code }}
            {% else %}
                {% journal_base_url article.journal %}{% url 'wjs_evaluate_review' assignment_id=review_assignment.id %}?access_code={{ review_assignment.access_code }}
            {% endif %}
            <br/><br/>
            This review assignment is due on {{ review_assignment.date_due|date:"Y-m-d" }}.  <br/><br/>
            {{ article_details }}<br/><br/>Regards,<br/>{{ request.user.signature|safe }}'
            """,
            "translations": {},
        }
        patch_setting(review_message_email_setting, review_message_setting_value)

    def author_can_contact_director():
        author_can_contact_director_setting: SettingParams = {
            "name": "author_can_contact_director",
            "group": wjs_review_settings_group,
            "types": "boolean",
            "pretty_name": _("Whether the author of a paper can contact the director"),
            "description": _(
                "The communication system will allow an author of a paper to directly contact the director of the journal only if this setting is true.",
            ),
            "is_translatable": False,
        }
        author_can_contact_director_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "",
            "translations": {},
        }
        create_customization_setting(
            author_can_contact_director_setting,
            author_can_contact_director_setting_value,
            author_can_contact_director_setting["name"],
        )

    acceptance_due_date()
    review_lists_page_size()
    review_invitation_message()
    declined_review_message()
    do_review_message()
    patch_review_message()
    author_can_contact_director()
