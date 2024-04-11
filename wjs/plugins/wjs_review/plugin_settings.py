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


def set_default_plugin_settings(force: bool = False):
    """Create default settings for the plugin."""
    try:
        wjs_review_settings_group = get_group("wjs_review")
    except SettingGroup.DoesNotExist:
        wjs_review_settings_group = SettingGroup.objects.create(name="wjs_review", enabled=True)
    try:
        wjs_prophy_settings_group = get_group("wjs_prophy")
    except SettingGroup.DoesNotExist:
        wjs_prophy_settings_group = SettingGroup.objects.create(name="wjs_prophy", enabled=True)
    email_settings_group = get_group("email")
    email_subject_settings_group = get_group("email_subject")
    general_group = get_group("general")

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
            acceptance_days_setting, acceptance_days_setting_value, acceptance_days_setting["name"], force=force
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
            force=force,
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
            "value": """
            Dear Colleague,
            {% if already_reviewed %}
                I am writing to ask for your help in reviewing the revised version of "{{ article.title }}" for which you have been so kind as to review the previous version.
            {%else %}
                I am writing to ask for your help in reviewing the {{ article.section.name }} "{{ article.title }}" for {{ journal.code }}.
            {% endif %}
            Please find the automatically generated instructions for reviewers appended below.<br><br>
            In the hope that you will accept my request, I would like to thank you in advance for your cooperation.<br><br>
            Kind regards,
            {{ request.user.signature|safe }}
            JCOM Editor-in-charge
            """,
            "translations": {},
        }
        create_customization_setting(
            review_invitation_message_setting,
            review_invitation_message_setting_value,
            review_invitation_message_setting["name"],
            force=force,
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
            force=force,
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
            force=force,
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
            force=force,
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
            force=force,
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
            force=force,
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
            force=force,
        )

    def revision_request_postpone_date_due_messages():
        revision_request_date_due_postponed_subject_setting: SettingParams = {
            "name": "revision_request_date_due_postponed_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for revision request due date postponing notification"),
            "description": _(
                "Subject of the email sent to the author when an editor postpones the revision due date.",
            ),
            "is_translatable": False,
        }
        revision_request_date_due_postponed_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Revision due date is postponed"),
            "translations": {},
        }
        create_customization_setting(
            revision_request_date_due_postponed_subject_setting,
            revision_request_date_due_postponed_subject_setting_value,
            revision_request_date_due_postponed_subject_setting["name"],
        )
        revision_request_date_due_postponed_body_setting: SettingParams = {
            "name": "revision_request_date_due_postponed_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for revision request due date postponing notification"),
            "description": _(
                "Body of the email sent to the author when an editor postpones the revision due date.",
            ),
            "is_translatable": False,
        }
        revision_request_date_due_postponed_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ article.correspondence_author.full_name }},
            The revision request due date for the article "{{ article.title }}" has been postponed until {{ date_due }}.
            Regards,
            the editor.
            """,
            "translations": {},
        }
        create_customization_setting(
            revision_request_date_due_postponed_body_setting,
            revision_request_date_due_postponed_body_setting_value,
            revision_request_date_due_postponed_body_setting["name"],
        )
        # setting_name = "revision_request_date_due_far_future_subject",
        revision_request_date_due_far_future_subject_setting: SettingParams = {
            "name": "revision_request_date_due_far_future_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for revision request due date postponing too far in the future notification"),
            "description": _(
                "Subject of the notification sent to EO when an editor postpones the revision due date too far in the future.",
            ),
            "is_translatable": False,
        }
        revision_request_date_due_far_future_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Revision request due date is postponed too far in the future"),
            "translations": {},
        }
        create_customization_setting(
            revision_request_date_due_far_future_subject_setting,
            revision_request_date_due_far_future_subject_setting_value,
            revision_request_date_due_far_future_subject_setting["name"],
        )
        revision_request_date_due_far_future_body_setting: SettingParams = {
            "name": "revision_request_date_due_far_future_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _(
                "Default message for revision request due date postponing too far in the future notification"
            ),
            "description": _(
                "Body of the notification sent to EO when an editor postpones the revision due date too far in the future.",
            ),
            "is_translatable": False,
        }
        revision_request_date_due_far_future_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ EO.full_name }},
            The revision request due date for the article "{{ article.title }}" has been postponed too far in the
            future, to {{ date_due }}.
            Regards,
            {{ request.user.signature|safe }}
            """,
            "translations": {},
        }
        create_customization_setting(
            revision_request_date_due_far_future_body_setting,
            revision_request_date_due_far_future_body_setting_value,
            revision_request_date_due_far_future_body_setting["name"],
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
            force=force,
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
            force=force,
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
            force=force,
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

    def admin_deems_unimportant():
        requeue_article_subject_setting: SettingParams = {
            "name": "requeue_article_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for requeue article for assignment"),
            "description": _(
                "The subject of the system message that is logged when EO verifies that an article's issues are not important and the article is requeued for editor assignment.",
            ),
            "is_translatable": False,
        }
        requeue_article_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Article issues deemed not important"),
            "translations": {},
        }
        create_customization_setting(
            requeue_article_subject_setting,
            requeue_article_subject_setting_value,
            requeue_article_subject_setting["name"],
        )
        requeue_article_message_setting: SettingParams = {
            "name": "requeue_article_message",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Automatic message for article requeue after issues verification"),
            "description": _(
                "The body of the system message that is logged when EO verifies that an article's issues are not important and the article is requeued for editor assignment.",
            ),
            "is_translatable": False,
        }
        requeue_article_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("EO has deemed paper issues not important."),
            "translations": {},
        }
        create_customization_setting(
            requeue_article_message_setting,
            requeue_article_message_setting_value,
            requeue_article_message_setting["name"],
        )

    def admin_requires_resubmission():
        requires_resubmission_subject_setting: SettingParams = {
            "name": "review_decision_requires_resubmission_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for article requires resubmission after issues verification"),
            "description": _(
                "The subject of the system message that is logged when EO verifies that an article requires resubmission.",
            ),
            "is_translatable": False,
        }
        requires_resubmission_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Article issues requires resubmission"),
            "translations": {},
        }
        create_customization_setting(
            requires_resubmission_subject_setting,
            requires_resubmission_subject_setting_value,
            requires_resubmission_subject_setting["name"],
        )
        requires_resubmission_message_setting: SettingParams = {
            "name": "review_decision_requires_resubmission_message",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Automatic message for article requires resubmission after issues verification"),
            "description": _(
                "The body of the system message that is logged when EO verifies that an article requires resubmission.",
            ),
            "is_translatable": False,
        }
        requires_resubmission_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("EO has deemed article requires resubmission."),
            "translations": {},
        }
        create_customization_setting(
            requires_resubmission_message_setting,
            requires_resubmission_message_setting_value,
            requires_resubmission_message_setting["name"],
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
            force=force,
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
            force=force,
        )

    def patch_review_settings():
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
            value='Request to review "{{ article.title }}"',
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
            <p>
            <br><br>
            {{ user_message_content|safe }}
            </p>
            <p>---------------------------------------</p>
            <p><b>{{ article.section.name }} to review:</b><br>
            {{ article_details }}<br>
            <b>Link to web page:</b><br>
            {% if reviewer.jcomprofile.invitation_token %}
                <a href="{% journal_base_url article.journal %}{% url 'wjs_evaluate_review' assignment_id=review_assignment.id token=reviewer.jcomprofile.invitation_token %}?access_code={{ review_assignment.access_code }}">Click here to review</a>
            {% else %}
                <a href="{% journal_base_url article.journal %}{% url 'wjs_evaluate_review' assignment_id=review_assignment.id%}?access_code={{ review_assignment.access_code }}">Click here to review</a>
            {% endif %}
            <br>
            </p>
            <p><b>Please accept/decline this request to review by {{ acceptance_due_date|date:"Y-m-d" }}.</b></p>
            <p>
            {% if already_reviewed %}
                <br>
            {% else %}
                {{ journal.code }} is a diamond open access journal focusing on research in science communication.<br>
                Its scope is available on [link to a specific help section for the journal in question].<br><br>
                Its editorial board (the name links to the relevant webpage) relies on the
                goodwill of referees to ensure the quality of the manuscripts it
                publishes and hopes that you will be able to help on this occasion.<br>
                More information about the Journal’s ethical and financial policy are
                available on [link to a specific help section for the journal in question]<br><br>
                It is {{ journal.code }}’s policy that authors and referees remain anonymous to each other.<br>
            {% endif %}
            <br>The {{ article.section.name }} you are being asked to review is available on the link provided above,
            together with the buttons to accept or decline this assignment and tools to communicate with the
            Editor in charge {{ request.user.signature|safe }}. <br><br>
            All the necessary information and instructions to do the review are available at:<br>
            [link to pdf file]<br><br>
            Do not hesitate to contact {{ request.user.signature|safe }} or the Editorial Office for any further information or assistance that you may need.
            </p>
            """,
            "translations": {},
        }
        patch_setting(review_message_email_setting, review_message_setting_value)
        default_review_days_setting: SettingParams = {
            "name": "default_review_days",
            "group": general_group,
            "types": "number",
            "pretty_name": _("Default number of days for review"),
            "description": _(
                "The default number of days before a review assignment is due.",
            ),
            "is_translatable": False,
        }
        default_review_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 5,
            "translations": {},
        }
        if force:
            patch_setting(default_review_days_setting, default_review_days_setting_value)

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
            force=force,
        )

    def prophy_settings():
        prophy_journal_setting: SettingParams = {
            "name": "prophy_journal",
            "group": wjs_prophy_settings_group,
            "types": "char",
            "pretty_name": _("Journal directory on prophy site"),
            "description": _(
                "The folder on Prophy site which contains the papers sent from the journal.",
            ),
            "is_translatable": False,
        }
        prophy_journal_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "",
            "translations": {},
        }
        create_customization_setting(
            prophy_journal_setting,
            prophy_journal_setting_value,
            prophy_journal_setting["name"],
        )
        prophy_upload_enabled_setting: SettingParams = {
            "name": "prophy_upload_enabled",
            "group": wjs_prophy_settings_group,
            "types": "boolean",
            "pretty_name": _("Enables Prophy upload"),
            "description": _(
                "Enables the journal to upload pdf files to prophy.",
            ),
            "is_translatable": False,
        }
        prophy_upload_enabled_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": False,
            "translations": {},
        }
        create_customization_setting(
            prophy_upload_enabled_setting,
            prophy_upload_enabled_setting_value,
            prophy_upload_enabled_setting["name"],
        )

    def due_date_postpone_message():
        subject_due_date_postpone_setting: SettingParams = {
            "name": "due_date_postpone_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for postponing due date notification"),
            "description": _(
                "The subject of the notification that is sent to the reviewer, when the editor postpones the report due date",
            ),
            "is_translatable": False,
        }
        subject_due_date_postpone_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Reviewer report due date is postponed"),
            "translations": {},
        }
        create_customization_setting(
            subject_due_date_postpone_setting,
            subject_due_date_postpone_setting_value,
            subject_due_date_postpone_setting["name"],
        )
        due_date_postpone_setting: SettingParams = {
            "name": "due_date_postpone_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for postponing due date notification"),
            "description": _(
                "The body of the notification that is sent to the reviewer, when the editor postpones the report due date",
            ),
            "is_translatable": False,
        }
        due_date_postpone_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ reviewer.full_name }},
            We are inform you that the reviewer report due date for the article "{{ article.title }}" is postponed to
            {{ date_due }}.
            Regards,
            {{ request.user.signature|safe }}
            """,
            "translations": {},
        }
        create_customization_setting(
            due_date_postpone_setting,
            due_date_postpone_setting_value,
            due_date_postpone_setting["name"],
        )

    def due_date_far_future_message():
        subject_due_date_far_future_setting: SettingParams = {
            "name": "due_date_far_future_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for notification when due date is postponed far in the future."),
            "description": _(
                "The subject of a notification that the system sends to EO when an editor postpones a reviewer report due date fare into the future.",
            ),
            "is_translatable": False,
        }
        subject_due_date_far_future_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Editor postpones reviewer report due date far in the future."),
            "translations": {},
        }
        create_customization_setting(
            subject_due_date_far_future_setting,
            subject_due_date_far_future_setting_value,
            subject_due_date_far_future_setting["name"],
        )
        due_date_far_future_setting: SettingParams = {
            "name": "due_date_far_future_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for when due date is postponed in the far future."),
            "description": _(
                "The subject of a notification that the system sends to EO when an editor postpones a reviewer report due date"
                "far into the future.",
            ),
            "is_translatable": False,
        }
        due_date_far_future_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ EO.full_name }},
            I inform you that the reviewer report due date for the article "{{ article.title }}" is postponed to
            {{ date_due }}.
            Regards,
            {{ request.user.signature|safe }}
            """,
            "translations": {},
        }
        create_customization_setting(
            due_date_far_future_setting,
            due_date_far_future_setting_value,
            due_date_far_future_setting["name"],
        )

    def editor_decline_assignment_message():
        subject_editor_decline_assignment_setting: SettingParams = {
            "name": "editor_decline_assignment_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for declination of Editor assignment"),
            "description": _(
                "The subject of the notification that is sent to the director when an editor declines an assignment.",
            ),
            "is_translatable": False,
        }
        subject_editor_decline_assignment_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Editor assignment is declined"),
            "translations": {},
        }
        create_customization_setting(
            subject_editor_decline_assignment_setting,
            subject_editor_decline_assignment_setting_value,
            subject_editor_decline_assignment_setting["name"],
        )
        editor_decline_assignment_setting: SettingParams = {
            "name": "editor_decline_assignment_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for declination of Editor assignment"),
            "description": _(
                "The body of the notification that is sent to the director when an editor declines an assignment.",
            ),
            "is_translatable": False,
        }
        editor_decline_assignment_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """
            Dear {{ director }},
            I'm sorry to inform you that I cant fulfill your request. My assigment is declined for the following
            reasons:
            Regards,
            {{ editor }}
            """,
            "translations": {},
        }
        create_customization_setting(
            editor_decline_assignment_setting,
            editor_decline_assignment_setting_value,
            editor_decline_assignment_setting["name"],
        )

    def typesetter_is_assigned_message():
        subject_typesetting_assignment: SettingParams = {
            "name": "typesetting_assignment_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for typesetting assignment."),
            "description": _(
                "The subject of the notification that is sent to the typesetter when he is assigned to an article.",
            ),
            "is_translatable": False,
        }
        subject_typesetting_assignment_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Typesetter is assigned to an article"),
            "translations": {},
        }
        create_customization_setting(
            subject_typesetting_assignment,
            subject_typesetting_assignment_setting_value,
            subject_typesetting_assignment["name"],
        )
        typesetter_is_assigned_setting: SettingParams = {
            "name": "typesetting_assignment_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for assignment of a Typesetter."),
            "description": _(
                "The body of the notification that is sent to the typesetter when he is assigned to an article.",
            ),
            "is_translatable": False,
        }
        typesetter_is_assigned_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ typesetter.full_name }}, you have been assigned paper {{ article.id }}.

            Please visit:
            {% url "wjs_article_details" article.id %}
            """,
            "translations": {},
        }
        create_customization_setting(
            typesetter_is_assigned_setting,
            typesetter_is_assigned_setting_value,
            typesetter_is_assigned_setting["name"],
        )

    # FIXME: Please correct subject and body contents
    def article_requires_proofreading_message():
        subject_proofreading_request: SettingParams = {
            "name": "proofreading_request_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for Proofreading request."),
            "description": _(
                "The subject of the notification that is sent to the Author when the paper has been typesetted and is "
                "ready for proofreading.",
            ),
            "is_translatable": False,
        }
        subject_proofreading_request_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": _("Article {{ article.pk }} ready for proofreading."),
            "translations": {},
        }
        create_customization_setting(
            subject_proofreading_request,
            subject_proofreading_request_setting_value,
            subject_proofreading_request["name"],
        )
        proofreading_request_setting: SettingParams = {
            "name": "proofreading_request_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for request of Author's proofreading."),
            "description": _(
                "The body of the notification that is sent to to the Author when the paper has been typesetted and is "
                "ready for proofreading.",
            ),
            "is_translatable": False,
        }
        proofreading_request_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ author.full_name }}, your article {article.title} has been typesetted and is ready
            for your check.

            Please visit:
            {% url "wjs_article_details" article.id %}
            """,
            "translations": {},
        }
        create_customization_setting(
            proofreading_request_setting,
            proofreading_request_setting_value,
            proofreading_request_setting["name"],
        )

    acceptance_due_date()
    review_lists_page_size()
    review_invitation_message()
    declined_review_message()
    do_review_message()
    patch_review_settings()
    review_decision_revision_request_message()
    review_decision_not_suitable_message()
    revision_request_postpone_date_due_messages()
    withdraw_review_message()
    technical_revision_body()
    author_can_contact_director()
    hijack_notification_message()
    author_submits_revision_message()
    admin_deems_unimportant()
    admin_requires_resubmission()
    prophy_settings()
    due_date_postpone_message()
    due_date_far_future_message()
    editor_decline_assignment_message()
    typesetter_is_assigned_message()
    article_requires_proofreading_message()


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
