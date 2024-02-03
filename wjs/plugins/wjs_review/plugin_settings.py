from pathlib import Path
from typing import Any, Dict

from core.models import SettingGroup
from django.utils.translation import gettext_lazy as _
from utils import plugins
from utils.logger import get_logger
from utils.setting_handler import save_setting

from wjs.jcom_profile.custom_settings_utils import (
    SettingParams,
    SettingValueParams,
    create_customization_setting,
    get_group,
    patch_setting,
)

logger = get_logger(__name__)

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
    ensure_workflow_elements()


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
    email_subject_settings_group = get_group("email_subject")

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

    def review_decision_revision_request_message():
        subject_review_decision_revision_request_setting: SettingParams = {
            "name": "review_decision_revision_request_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for revision request notification"),
            "description": _(
                "Provide context for the notification when the Editor requests a major/minor revision for an article.",
            ),
            "is_translatable": False,
        }
        subject_review_decision_revision_request_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _(
                "{% if major_revision %}Editor requires revision{% elif minor_revision %}Editor requires (minor) revision{% elif tech_revision %}Editor enables metadata update{% endif %}"
            ),
            "translations": {},
        }
        create_customization_setting(
            subject_review_decision_revision_request_setting,
            subject_review_decision_revision_request_setting_value,
            subject_review_decision_revision_request_setting["name"],
        )
        review_decision_revision_request_setting: SettingParams = {
            "name": "review_decision_revision_request_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for revision request notification"),
            "description": _(
                "Provide context for the notification when the Editor requests a major/minor revision for an article.",
            ),
            "is_translatable": False,
        }
        review_decision_revision_request_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ article.correspondence_author.full_name }},
            {{ editor.full_name }} has requested a {% if minor_revision %}minor{% endif %} revision of {{ article.title }}.
            You can view your reviews and feedback on the manuscript at: {{ review_url }}
            Regards,
            {{ request.user.signature|safe }}
            """,
            "translations": {},
        }
        create_customization_setting(
            review_decision_revision_request_setting,
            review_decision_revision_request_setting_value,
            review_decision_revision_request_setting["name"],
        )

    def review_decision_not_suitable_message():
        subject_review_decision_not_suitable_setting: SettingParams = {
            "name": "review_decision_not_suitable_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for article not suitable decision notification"),
            "description": _(
                "Provide context for the notification when the article is deemed not suitable.",
            ),
            "is_translatable": False,
        }
        subject_review_decision_not_suitable_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Article is deemed not suitable"),
            "translations": {},
        }
        create_customization_setting(
            subject_review_decision_not_suitable_setting,
            subject_review_decision_not_suitable_setting_value,
            subject_review_decision_not_suitable_setting["name"],
        )
        review_decision_not_suitable_setting: SettingParams = {
            "name": "review_decision_not_suitable_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for article not suitable decision notification"),
            "description": _(
                "Provide context for the notification when the article is deemed not suitable.",
            ),
            "is_translatable": False,
        }
        review_decision_not_suitable_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ article.correspondence_author.full_name }},
            We are sorry to inform you that "{{ article.title }}" has been deemed not suitable for {{ article.journal.name }}.
            You can view your reviews and feedback on the manuscript at: {{ review_url }}
            Regards,
            {{ request.user.signature|safe }}
            """,
            "translations": {},
        }
        create_customization_setting(
            review_decision_not_suitable_setting,
            review_decision_not_suitable_setting_value,
            review_decision_not_suitable_setting["name"],
        )

    def withdraw_review_message():
        withdraw_review_subject_setting: SettingParams = {
            "name": "review_withdraw_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for review withdraw notification"),
            "description": _(
                "Provide context for automatic review withdraw.",
            ),
            "is_translatable": False,
        }
        withdraw_review_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Review withdraw notice"),
            "translations": {},
        }
        create_customization_setting(
            withdraw_review_subject_setting,
            withdraw_review_subject_setting_value,
            withdraw_review_subject_setting["name"],
        )
        withdraw_review_message_setting: SettingParams = {
            "name": "review_withdraw_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Default message for review withdraw notification"),
            "description": _(
                "Provide context for automatic review withdraw.",
            ),
            "is_translatable": False,
        }
        withdraw_review_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _(
                "The review has been withdrawn because the article is undergoing a revision.<br>{{ withdraw_notice }}"
            ),
            "translations": {},
        }
        create_customization_setting(
            withdraw_review_message_setting,
            withdraw_review_message_setting_value,
            withdraw_review_message_setting["name"],
        )
        withdraw_notice_setting: SettingParams = {
            "name": "review_withdraw_notice",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Default message for review withdraw notification"),
            "description": _(
                "Provide context for automatic review withdraw.",
            ),
            "is_translatable": False,
        }
        withdraw_notice_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Provide context for the decision."),
            "translations": {},
        }
        create_customization_setting(
            withdraw_notice_setting,
            withdraw_notice_setting_value,
            withdraw_notice_setting["name"],
        )

    def technical_revision_body():
        technical_revision_subject_setting: SettingParams = {
            "name": "technical_revision_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for technical revision request"),
            "description": _(
                "Provide context for technical revision.",
            ),
            "is_translatable": False,
        }
        technical_revision_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Technical revision request"),
            "translations": {},
        }
        create_customization_setting(
            technical_revision_subject_setting,
            technical_revision_subject_setting_value,
            technical_revision_subject_setting["name"],
        )
        technical_revision_body_setting: SettingParams = {
            "name": "technical_revision_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Automatic message for technical revision request"),
            "description": _(
                "Provide context for technical revision request.",
            ),
            "is_translatable": False,
        }
        technical_revision_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Editor has requested a technical revision, you can now edit article metadata."),
            "translations": {},
        }
        create_customization_setting(
            technical_revision_body_setting,
            technical_revision_body_setting_value,
            technical_revision_body_setting["name"],
        )

    def author_submits_revision_message():
        revision_submission_subject_setting: SettingParams = {
            "name": "revision_submission_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for submission of author revision"),
            "description": _(
                "Provide context for technical revision.",
            ),
            "is_translatable": False,
        }
        revision_submission_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Author revision submission"),
            "translations": {},
        }
        create_customization_setting(
            revision_submission_subject_setting,
            revision_submission_subject_setting_value,
            revision_submission_subject_setting["name"],
        )
        revision_submission_message_setting: SettingParams = {
            "name": "revision_submission_message",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Automatic message for author revision submission"),
            "description": _(
                "Provide context for author revision submission.",
            ),
            "is_translatable": False,
        }
        revision_submission_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Author has submitted a revision of their article, you can now check edited content."),
            "translations": {},
        }
        create_customization_setting(
            revision_submission_message_setting,
            revision_submission_message_setting_value,
            revision_submission_message_setting["name"],
        )

    def hijack_notification_message():
        hijack_notification_subject: SettingParams = {
            "name": "hijack_notification_subject",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Default subject for notifications of actions as hijacked users"),
            "description": _(
                "Provide context for notifications of actions as hijacked users.",
            ),
            "is_translatable": False,
        }
        hijack_notification_subject_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("User {{ hijacker }} executed {{ original_subject }}"),
            "translations": {},
        }
        create_customization_setting(
            hijack_notification_subject,
            hijack_notification_subject_value,
            hijack_notification_subject["name"],
        )
        hijack_notification_body: SettingParams = {
            "name": "hijack_notification_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Default message for notifications of actions as hijacked users"),
            "description": _(
                "Provide context for notifications of actions as hijacked users.",
            ),
            "is_translatable": False,
        }
        hijack_notification_body_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("User {{ hijacker }} executed {{ original_subject }} impersonating you."),
            "translations": {},
        }
        create_customization_setting(
            hijack_notification_body,
            hijack_notification_body_value,
            hijack_notification_body["name"],
        )

    def patch_review_messages():
        editor_assignment_subject_setting: SettingParams = {
            "name": "subject_editor_assignment",
            "group": email_subject_settings_group,
            "types": "text",
            "pretty_name": _("Subject of the assign to editor message"),
            "description": _(
                "Provide instructions to handle editor assignments",
            ),
            "is_translatable": False,
        }
        editor_assignment_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            # https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/267#note_11875
            "value": "{{ article.id }} assigned",
            "translations": {},
        }
        patch_setting(editor_assignment_subject_setting, editor_assignment_subject_setting_value)
        editor_assignment_body_setting: SettingParams = {
            "name": "editor_assignment",
            "group": email_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body of the assign to editor message"),
            "description": _(
                "Provide instructions to handle editor assignments",
            ),
            "is_translatable": False,
        }
        editor_assignment_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ editor.full_name }},
            <br/><br/>
            You have been assigned as an editor to "{{ article.title }}" in the journal {{ request.journal.name }}.
            <br/><br/>
            If you are unable to be the editor for this article, please reply to this email.
            <br/><br/>
            You can view this article's data on the journal site: {{ review_in_review_url }}
            <br/><br/>
            Regards,
            <br/><br/>
            {{ request.user.signature|safe }}
            """,
            "translations": {},
        }
        patch_setting(editor_assignment_body_setting, editor_assignment_body_setting_value)
        save_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_assignment",
            journal=None,
            value="Editor assigns reviewer",
        )
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
            {{ user_message_content }}
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
    patch_review_messages()
    review_decision_revision_request_message()
    review_decision_not_suitable_message()
    withdraw_review_message()
    technical_revision_body()
    author_can_contact_director()
    hijack_notification_message()
    author_submits_revision_message()


def ensure_workflow_elements():
    """Ensure that WJS's workflow element is the first element in all journals."""
    from core.models import Workflow, WorkflowElement
    from journal.models import Journal

    for journal in Journal.objects.all():
        journal_workflow = Workflow.objects.get(journal=journal)

        element_name = PLUGIN_NAME
        if journal_workflow.elements.filter(element_name=element_name).exists():
            # Our wf element is already there: do nothing
            return

        defaults = {
            "handshake_url": HANDSHAKE_URL,
            "stage": STAGE,
            "article_url": ARTICLE_PK_IN_HANDSHAKE_URL,
            "jump_url": JUMP_URL,
            "order": 0,
        }

        element_obj_to_add, created = WorkflowElement.objects.get_or_create(
            journal=journal,
            element_name=element_name,
            defaults=defaults,
        )

        if created:
            logger.info(f"Created workflow element {element_obj_to_add.element_name}")

        # Put our wf element at the beginning of the list
        # (remember that it has order=0 on a PositiveIntegerfield)
        for element in journal_workflow.elements.all():
            element.order += 1
            element.save()

        journal_workflow.elements.add(element_obj_to_add)
