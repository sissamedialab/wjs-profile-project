from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.models import Setting, SettingGroup, SettingValue
from django.utils.translation import gettext_lazy as _
from utils import plugins
from utils.logger import get_logger
from utils.setting_handler import save_setting

from wjs.jcom_profile.custom_settings_utils import (
    SettingParams,
    SettingValueParams,
    create_customization_setting,
    export_to_csv_manager,
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

    def acceptance_due_date() -> tuple[SettingValue, ...]:
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
        return (
            create_customization_setting(
                acceptance_days_setting, acceptance_days_setting_value, acceptance_days_setting["name"], force=force
            ),
        )

    def review_lists_page_size() -> tuple[SettingValue, ...]:
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
        return (
            create_customization_setting(
                review_lists_page_size_setting,
                review_lists_page_size_setting_value,
                review_lists_page_size_setting["name"],
                force=force,
            ),
        )

    def declined_review_notice() -> tuple[SettingValue, ...]:
        setting: SettingParams = {
            "name": "declined_review_notice",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Message shown when reviewer declines the review"),
            "description": _(
                "Provide a thank you message when reviewer declines the review.",
            ),
            "is_translatable": False,
        }
        value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Thank you for considering the Editor's invite.",
            "translations": {},
        }
        return (
            create_customization_setting(
                setting,
                value,
                setting["name"],
                force=force,
            ),
        )

    def review_decision_not_suitable_message() -> tuple[SettingValue, ...]:
        subject_review_decision_not_suitable_setting: SettingParams = {
            "name": "review_decision_not_suitable_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for article not suitable decision notification"),
            "description": _(
                "Subject of the notification sent to the author when the article is deemed not suitable.",
            ),
            "is_translatable": False,
        }
        subject_review_decision_not_suitable_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Declared not suitable",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_review_decision_not_suitable_setting,
            subject_review_decision_not_suitable_setting_value,
            subject_review_decision_not_suitable_setting["name"],
            force=force,
        )
        review_decision_not_suitable_setting: SettingParams = {
            "name": "review_decision_not_suitable_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of the article-not-suitable message"),
            "description": _(
                "Body of the notification sent to the author when the article is deemed not suitable.",
            ),
            "is_translatable": False,
        }
        review_decision_not_suitable_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Dr. {{ article.correspondence_author.full_name }},
<br><br>
We regret to inform you that the Editor in charge of your {{ article.section.name }} [...] considers it not suitable for {{ article.journal.code }}.
The Editor review is available to you at the <a href="{{ article.articleworkflow.url }}">{{ article.section.name }} web page</a>.
<br><br>
Thank you and best regards,
<br>
{{ journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            review_decision_not_suitable_setting,
            review_decision_not_suitable_setting_value,
            review_decision_not_suitable_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def revision_request_postpone_date_due_messages() -> tuple[SettingValue, ...]:
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
            "value": "Due date postponed",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            revision_request_date_due_postponed_subject_setting,
            revision_request_date_due_postponed_subject_setting_value,
            revision_request_date_due_postponed_subject_setting["name"],
            force=force,
        )
        revision_request_date_due_postponed_body_setting: SettingParams = {
            "name": "revision_request_date_due_postponed_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of the revision request due date postponing notification"),
            "description": _(
                "Body of the email sent to the author when an editor postpones the revision due date.",
            ),
            "is_translatable": False,
        }
        revision_request_date_due_postponed_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Dr. {{ article.correspondence_author.full_name }},
<br><br>
The deadline for revising your {{ article.section.name }} has been postponed until {{ date_due }}.
<br><br>
Regards,<br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            revision_request_date_due_postponed_body_setting,
            revision_request_date_due_postponed_body_setting_value,
            revision_request_date_due_postponed_body_setting["name"],
            force=force,
        )

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
            "value": "Due date postponed considerably",
            "translations": {},
        }
        setting_3 = create_customization_setting(
            revision_request_date_due_far_future_subject_setting,
            revision_request_date_due_far_future_subject_setting_value,
            revision_request_date_due_far_future_subject_setting["name"],
            force=force,
        )
        revision_request_date_due_far_future_body_setting: SettingParams = {
            "name": "revision_request_date_due_far_future_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of the revision request due date postponing too far in the future notification"),
            "description": _(
                "Body of the notification sent to EO when an editor postpones the revision due date too far in the future.",
            ),
            "is_translatable": False,
        }
        revision_request_date_due_far_future_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ EO.full_name }},
<br>
The revision due date for the article "{{ article.title }}" has been postponed to {{ date_due }}.
<br>
Since it is far in the future it might be worth checking.
<br><br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_4 = create_customization_setting(
            revision_request_date_due_far_future_body_setting,
            revision_request_date_due_far_future_body_setting_value,
            revision_request_date_due_far_future_body_setting["name"],
            force=force,
        )
        return setting_1, setting_2, setting_3, setting_4

    def technical_revision_body() -> tuple[SettingValue, ...]:
        technical_revision_subject_setting: SettingParams = {
            "name": "technical_revision_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for technical revision request"),
            "description": _(
                "Subject of the notification sent to author when a technical revision has be requested.",
            ),
            "is_translatable": False,
        }
        technical_revision_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Metadata update allowed",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            technical_revision_subject_setting,
            technical_revision_subject_setting_value,
            technical_revision_subject_setting["name"],
            force=force,
        )
        technical_revision_body_setting: SettingParams = {
            "name": "technical_revision_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body of technical revision request notice"),
            "description": _(
                "Body of the notification sent to the author when a technical revision has been requested.",
            ),
            "is_translatable": False,
        }
        technical_revision_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "The {{ article.journal.code }} Editor in charge has allowed you to edit [...] your manuscript metadata. Please do so by {{ revision.date_due }}.",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            technical_revision_body_setting,
            technical_revision_body_setting_value,
            technical_revision_body_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def admin_deems_unimportant() -> tuple[SettingValue, ...]:
        requeue_article_subject_setting: SettingParams = {
            "name": "requeue_article_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for article requeue after issues verification notice"),
            "description": _(
                "The subject of the system message that is logged when EO verifies that an article's issues are not important and the article is requeued for editor assignment.",
            ),
            "is_translatable": False,
        }
        requeue_article_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "No blocking issues found",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            requeue_article_subject_setting,
            requeue_article_subject_setting_value,
            requeue_article_subject_setting["name"],
            force=force,
        )
        requeue_article_message_setting: SettingParams = {
            "name": "requeue_article_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body of article requeue after issues verification notice"),
            "description": _(
                "The body of the system message that is logged when EO verifies that an article's issues are not important and the article is requeued for editor assignment.",
            ),
            "is_translatable": False,
        }
        requeue_article_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "This submission has been checked for possible issues. The review process may start.",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            requeue_article_message_setting,
            requeue_article_message_setting_value,
            requeue_article_message_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def admin_requires_resubmission() -> tuple[SettingValue, ...]:
        requires_resubmission_subject_setting: SettingParams = {
            "name": "review_decision_requires_resubmission_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for article requires resubmission after issues verification"),
            "description": _(
                "The subject of notification to the author of papers that cannot start the review process and that require resubmission.",
            ),
            "is_translatable": False,
        }
        requires_resubmission_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Changes required",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            requires_resubmission_subject_setting,
            requires_resubmission_subject_setting_value,
            requires_resubmission_subject_setting["name"],
            force=force,
        )
        requires_resubmission_message_setting: SettingParams = {
            "name": "review_decision_requires_resubmission_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body fo the notification for article requires resubmission after issues verification"),
            "description": _(
                "The body of notification to the author of papers that cannot start the review process and that require resubmission.",
            ),
            "is_translatable": False,
        }
        requires_resubmission_message_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Dr. {{ author.full_name }},
<br><br>
Routine checks have spotted [...] issues in your {{ article.section.name }}.
<br>
More explanations will be provided in a separate message.
<br>
Once you have made the modifications and/or provided the explanations requested,
please resubmit your {{ article.section.name }} from its <a href="{{ article.articleworkflow.url }}">web page</a>.
<br><br>
Thank you and best regards,
<br><br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            requires_resubmission_message_setting,
            requires_resubmission_message_setting_value,
            requires_resubmission_message_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def hijack_notification_message() -> tuple[SettingValue, ...]:
        hijack_notification_subject: SettingParams = {
            "name": "hijack_notification_subject",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Subject for notifications of actions as hijacked users"),
            "description": _(
                "Subject of the notification sent to the hijacked user for actions done in his place.",
            ),
            "is_translatable": False,
        }
        hijack_notification_subject_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": '{{ journal.code }} Editorial Office /Editor in chief did "{{ original_subject }}"',
            "translations": {},
        }
        setting_1 = create_customization_setting(
            hijack_notification_subject,
            hijack_notification_subject_value,
            hijack_notification_subject["name"],
            force=force,
        )
        hijack_notification_body: SettingParams = {
            "name": "hijack_notification_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body for the notifications of actions as hijacked users"),
            "description": _(
                "Body of the notification sent to the hijacked user for actions done in his place.",
            ),
            "is_translatable": False,
        }
        hijack_notification_body_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """{{ hijacker }} has done the following action on your behalf:
<br>
{{ original_subject }}
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            hijack_notification_body,
            hijack_notification_body_value,
            hijack_notification_body["name"],
            force=force,
        )
        return setting_1, setting_2

    def core_review_settings() -> tuple[SettingValue, ...]:

        setting_3_p: SettingParams = {
            "name": "review_invitation_message_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body of the assign-to-reviewer message - non customizable part"),
            "description": _(
                """The body of the notification that is sent to the editor when a paper is assigned to him.
The part `{{ user_message_content }}` will be replaced with a text written by the editor (see review_assignment).
Replaces Janeway's review_assignment.
""",
            ),
            "is_translatable": False,
        }
        setting_3_v: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """{% load fqdn %}{% journal_base_url article.journal as base_url %}
<p>
<br><br>
{{ user_message_content|safe }}
</p>
<p>---------------------------------------</p>
<p><b>{{ article.section.name }} to review:</b><br>
{{ article.title }}
</p>
<p><b>Link to web page:</b><br>
{% if reviewer.jcomprofile.invitation_token %}
    <a href="{{ base_url }}{% url 'wjs_evaluate_review' assignment_id=review_assignment.id token=reviewer.jcomprofile.invitation_token %}?access_code={{ review_assignment.access_code }}">Click here to review</a>
{% else %}
    <a href="{{ base_url }}{% url 'wjs_evaluate_review' assignment_id=review_assignment.id %}?access_code={{ review_assignment.access_code }}">Click here to review</a>
{% endif %}
</p>
<p><b>Please accept/decline this invite to review by {{ acceptance_due_date|date:"Y-m-d" }}.</b></p>
<p>
{% if already_reviewed %}
    <br>
{% else %}
    {{ article.journal.name }} is a diamond-open-access journal focusing on research in science communication.<br>
    Its scope is available <a href="{{ base_url }}/site/about-jcom/#heading1">here</a>.
    <br><br>
    Its <a href="{{ base_url }}/site/editorial-team/">editorial board</a> relies on the
    goodwill of reviewers to ensure the quality of the manuscripts it
    publishes and hopes that you will be able to help on this occasion.
    <br>
    More information about the Journal’s ethical policy is
    available <a href="{{ base_url }}/site/about-jcom/#heading4">here</a>
    <br><br>
    It is {{ journal.code }}'s policy that authors and reviewers remain anonymous to each other.<br>
{% endif %}
<br>The {{ article.section.name }} you are being asked to review is available on the link provided above,
together with the buttons to accept or decline this invite and tools to communicate with the
Editor in charge.
<br><br>
All the necessary information and instructions to do the review are available <a href="{{ base_url }}/site/reviewers/">here</a>.
<br><br>
If you decide to cooperate with us, please keep in mind the specificities of this article type, which are explained
<a href="{{ base_url }}/site/authors/">here</a>.
<br><br>
Do not hesitate to contact the Editor in charge or the Editorial Office for any further information or assistance that you may need.
</p>
""",
            "translations": {},
        }
        setting_3 = create_customization_setting(
            setting_3_p,
            setting_3_v,
            setting_3_p["name"],
            force=force,
        )

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
            setting_4 = patch_setting(default_review_days_setting, default_review_days_setting_value)
        default_editor_assign_reviewer_days_setting: SettingParams = {
            "name": "default_editor_assign_reviewer_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default number of days for assign review"),
            "description": _(
                "The default number of days before editor should assign reviewer.",
            ),
            "is_translatable": False,
        }
        default_editor_assign_review_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 5,
            "translations": {},
        }
        setting_5 = create_customization_setting(
            default_editor_assign_reviewer_days_setting,
            default_editor_assign_review_days_setting_value,
            default_editor_assign_reviewer_days_setting["name"],
            force=force,
        )
        default_editor_make_decision_days_setting: SettingParams = {
            "name": "default_editor_make_decision_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default number of days for making a decision"),
            "description": _(
                "The default number of days before editor should make a decision.",
            ),
            "is_translatable": False,
        }
        default_editor_make_decision_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 5,
            "translations": {},
        }
        setting_6 = create_customization_setting(
            default_editor_make_decision_days_setting,
            default_editor_make_decision_days_setting_value,
            default_editor_make_decision_days_setting["name"],
            force=force,
        )
        default_author_major_revision_days_setting: SettingParams = {
            "name": "default_author_major_revision_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default number of days for major revision"),
            "description": _(
                "The default number of days for author to submit a major revision.",
            ),
            "is_translatable": False,
        }
        default_author_major_revision_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 60,
            "translations": {},
        }
        setting_7 = create_customization_setting(
            default_author_major_revision_days_setting,
            default_author_major_revision_days_setting_value,
            default_author_major_revision_days_setting["name"],
            force=force,
        )
        default_author_major_revision_days_max_setting: SettingParams = {
            "name": "default_author_major_revision_days_max",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default maximum number of days for major revision"),
            "description": _(
                "The default maximum number of days for author to submit a major revision.",
            ),
            "is_translatable": False,
        }
        default_author_major_revision_days_max_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 90,
            "translations": {},
        }
        setting_10 = create_customization_setting(
            default_author_major_revision_days_max_setting,
            default_author_major_revision_days_max_setting_value,
            default_author_major_revision_days_max_setting["name"],
            force=force,
        )
        # refs 1024
        default_author_minor_revision_days_setting: SettingParams = {
            "name": "default_author_minor_revision_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default number of days for minor revision"),
            "description": _(
                "The default number of days for author to submit a minor revision.",
            ),
            "is_translatable": False,
        }
        default_author_minor_revision_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 30,
            "translations": {},
        }
        setting_8 = create_customization_setting(
            default_author_minor_revision_days_setting,
            default_author_minor_revision_days_setting_value,
            default_author_minor_revision_days_setting["name"],
            force=force,
        )
        default_author_minor_revision_days_max_setting: SettingParams = {
            "name": "default_author_minor_revision_days_max",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default maximum number of days for minor revision"),
            "description": _(
                "The default maximum number of days for author to submit a minor revision.",
            ),
            "is_translatable": False,
        }
        default_author_minor_revision_days_max_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 60,
            "translations": {},
        }
        setting_11 = create_customization_setting(
            default_author_minor_revision_days_max_setting,
            default_author_minor_revision_days_max_setting_value,
            default_author_minor_revision_days_max_setting["name"],
            force=force,
        )
        # Keep this to make it simple to modify it in the settings' manager, if needed. Use it to automatically set
        # the date_due value when the editor requests a technical revision (without neither a form nor a modal).
        default_author_technical_revision_days_setting: SettingParams = {
            "name": "default_author_technical_revision_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default number of days for technical revision"),
            "description": _(
                "The default number of days for author to submit a technical revision.",
            ),
            "is_translatable": False,
        }
        default_author_technical_revision_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 2,
            "translations": {},
        }
        setting_9 = create_customization_setting(
            default_author_technical_revision_days_setting,
            default_author_technical_revision_days_setting_value,
            default_author_technical_revision_days_setting["name"],
            force=force,
        )
        # refs #1024
        default_author_appeal_revision_days_setting: SettingParams = {
            "name": "default_author_appeal_revision_days",
            "group": wjs_review_settings_group,
            "types": "number",
            "pretty_name": _("Default number of days for appeal"),
            "description": _(
                "The default number of days for author to respond to an appeal.",
            ),
            "is_translatable": False,
        }
        default_author_appeal_revision_days_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": 30,
            "translations": {},
        }
        setting_12 = create_customization_setting(
            default_author_appeal_revision_days_setting,
            default_author_appeal_revision_days_setting_value,
            default_author_appeal_revision_days_setting["name"],
            force=force,
        )
        return (
            setting_3,
            setting_5,
            setting_6,
            setting_7,
            setting_8,
            setting_9,
            setting_10,
            setting_11,
            setting_12,
        )

    def author_can_contact_director() -> tuple[SettingValue, ...]:
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
        return (
            create_customization_setting(
                author_can_contact_director_setting,
                author_can_contact_director_setting_value,
                author_can_contact_director_setting["name"],
                force=force,
            ),
        )

    def prophy_settings() -> tuple[SettingValue, ...]:
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
        setting_1 = create_customization_setting(
            prophy_journal_setting,
            prophy_journal_setting_value,
            prophy_journal_setting["name"],
            force=force,
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
        setting_2 = create_customization_setting(
            prophy_upload_enabled_setting,
            prophy_upload_enabled_setting_value,
            prophy_upload_enabled_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def due_date_postpone_message() -> tuple[SettingValue, ...]:
        subject_due_date_postpone_setting: SettingParams = {
            "name": "due_date_postpone_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for postponing due date notification"),
            "description": _(
                "The subject of the notification that is sent to the reviewer, when the editor postpones the due date",
            ),
            "is_translatable": False,
        }
        subject_due_date_postpone_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Review due date postponed",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_due_date_postpone_setting,
            subject_due_date_postpone_setting_value,
            subject_due_date_postpone_setting["name"],
            force=force,
        )
        due_date_postpone_setting: SettingParams = {
            "name": "due_date_postpone_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of the postponing due date notification"),
            "description": _(
                "The body of the notification that is sent to the reviewer, when the editor postpones the due date",
            ),
            "is_translatable": False,
        }
        due_date_postpone_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Dr. {{ reviewer.full_name }},
<br><br>
This is to inform you that your review due date for the {{ article.section.name }} "{{ article.title }}" has been postponed to
{{ date_due }}.
<br><br>
Thank you in advance for your cooperation and best regards,<br>
{{ journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            due_date_postpone_setting,
            due_date_postpone_setting_value,
            due_date_postpone_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def due_date_far_future_message() -> tuple[SettingValue, ...]:
        subject_due_date_far_future_setting: SettingParams = {
            "name": "due_date_far_future_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for notification when due date is postponed far in the future."),
            "description": _(
                "The subject of a notification that the system sends to EO when editors or reviewers postpone a review due date far into the future.",
            ),
            "is_translatable": False,
        }
        subject_due_date_far_future_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Review due date postponed considerably",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_due_date_far_future_setting,
            subject_due_date_far_future_setting_value,
            subject_due_date_far_future_setting["name"],
            force=force,
        )
        due_date_far_future_setting: SettingParams = {
            "name": "due_date_far_future_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of notification sent when due date is postponed in the far future."),
            "description": _(
                "The body of a notification that the system sends to EO when editors or reviewers postpone a review due date"
                "far into the future.",
            ),
            "is_translatable": False,
        }
        due_date_far_future_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ EO.full_name }},
<br><br>
{{ reviewer.full_name }}'s review due date for the {{ article.section.name }} "{{ article.title }}" has been postponed to {{ date_due }}.
Since it is far in the future it might be worth checking.
<br><br>
{{ journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            due_date_far_future_setting,
            due_date_far_future_setting_value,
            due_date_far_future_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def editor_decline_assignment_messages() -> tuple[SettingValue, ...]:
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
            "value": "Editor declined assignment",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_editor_decline_assignment_setting,
            subject_editor_decline_assignment_setting_value,
            subject_editor_decline_assignment_setting["name"],
            force=force,
        )
        editor_decline_assignment_setting: SettingParams = {
            "name": "editor_decline_assignment_default",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default body of the notification for declination of Editor assignment"),
            "description": _(
                "The default body of the notification that is sent to the director when an editor declines an assignment. This will be further edited by the editor.",
            ),
            "is_translatable": False,
        }
        editor_decline_assignment_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ director }},
<br><br>
I regret to inform you that [...] I am unable to handle the {{ article.section.name }} "{{ article.title }}" by {{ article.correspondence_author.full_name }} for the following reasons:
<br><br>
{{ decline_reason_label }}
<br><br>
{% if decline_text %}
Additional comments:
<br><br>
{{ decline_text }}
<br><br>
{% endif %}
Best regards,
<br><br>
{{ request.user.signature|safe }}<br>
{{ journal.code }} Editor in charge
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            editor_decline_assignment_setting,
            editor_decline_assignment_setting_value,
            editor_decline_assignment_setting["name"],
            force=force,
        )

        setting_3_params: SettingParams = {
            "name": "editor_decline_assignment__for_reviewers_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject of notification sent to active reviewers when editor declines assignment"),
            "description": _(
                "Subject of the notification sent to active reviewers when editor declines assignment.",
            ),
            "is_translatable": False,
        }
        setting_3_valueparams: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Invite to review withdrawn""",
            "translations": {},
        }
        setting_3 = create_customization_setting(
            setting_3_params,
            setting_3_valueparams,
            setting_3_params["name"],
            force=force,
        )

        setting_4_params: SettingParams = {
            "name": "editor_decline_assignment__for_reviewers_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Notification sent to active reviewers when editor declines assignment"),
            "description": _(
                "Body of the notification sent to active reviewers when editor declines assignment.",
            ),
            "is_translatable": False,
        }
        setting_4_valueparams: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Dr. {{ recipient.full_name }},
<br><br>
This is to inform you that you have been deselected as reviewer, hence your report is no longer needed.
<br><br>
{{ article.journal.code }} looks forward to having another opportunity to avail itself of your
expertise in the future.
<br><br>
Thank you and best regards,
<br><br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_4 = create_customization_setting(
            setting_4_params,
            setting_4_valueparams,
            setting_4_params["name"],
            force=force,
        )

        return setting_1, setting_2, setting_3, setting_4

    def editor_assigns_themselves_as_reviewer_message() -> tuple[SettingValue, ...]:
        wjs_editor_i_will_review_message_subject_setting: SettingParams = {
            "name": "wjs_editor_i_will_review_message_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject of the notification for self-selection of Editor as Reviewer"),
            "description": _(
                "The subject of the notification that is sent when an Editor self-selects as Reviewer.",
            ),
            "is_translatable": False,
        }
        wjs_editor_i_will_review_message_subject_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Editor will review",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            wjs_editor_i_will_review_message_subject_setting,
            wjs_editor_i_will_review_message_subject_setting_value,
            wjs_editor_i_will_review_message_subject_setting["name"],
            force=force,
        )
        wjs_editor_i_will_review_message_body_setting: SettingParams = {
            "name": "wjs_editor_i_will_review_message_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of the notification for self-selection of Editor as Reviewer"),
            "description": _(
                "The body of the notification that is sent when an Editor self-selects as Reviewer.",
            ),
            "is_translatable": False,
        }
        wjs_editor_i_will_review_message_body_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Editor {{ review_assignment.editor.full_name }} has decided to review {{ article.section.name }} "{{ article.title }}".""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            wjs_editor_i_will_review_message_body_setting,
            wjs_editor_i_will_review_message_body_setting_value,
            wjs_editor_i_will_review_message_body_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def eo_is_assigned_message() -> tuple[SettingValue, ...]:
        subject_eo_assignment: SettingParams = {
            "name": "eo_assignment_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for eo assignment."),
            "description": _(
                "The subject of the notification that is sent to the eo when he is assigned to an article.",
            ),
            "is_translatable": False,
        }
        subject_eo_assignment_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "EO assigned to an article",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_eo_assignment,
            subject_eo_assignment_setting_value,
            subject_eo_assignment["name"],
            force=force,
        )
        eo_is_assigned_setting: SettingParams = {
            "name": "eo_assignment_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("The body for for assignment of a eo notification."),
            "description": _(
                "The body of the notification that is sent to the eo when he is assigned to an article.",
            ),
            "is_translatable": False,
        }
        eo_is_assigned_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ eo.full_name }}, you have been assigned this {{ article.section.name }}.""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            eo_is_assigned_setting,
            eo_is_assigned_setting_value,
            eo_is_assigned_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def article_requires_proofreading_message() -> tuple[SettingValue, ...]:
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
            "value": "Ready for proofreading",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_proofreading_request,
            subject_proofreading_request_setting_value,
            subject_proofreading_request["name"],
            force=force,
        )
        proofreading_request_setting: SettingParams = {
            "name": "proofreading_request_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Body of the request of Author's proofreading notification."),
            "description": _(
                "The body of the notification that is sent to to the Author when the paper has been typesetted and is "
                "ready for proofreading.",
            ),
            "is_translatable": False,
        }
        proofreading_request_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Dr. {{ author.full_name }},
<br><br>
Please proof-read the pdf version of your typeset {{ article.section.name }} within 1 week [...].
<br><br>
Only a limited number of the following kind of corrections are acceptable at this stage:
<br><br>
<ul>
<li> layout and typesetting mistakes,
<li> spelling mistakes in words or formulas,
<li> mistakes or updating in references
</ul>
<br><br>
<strong>Important</strong>: please reply to the queries on the first page of your {{ article.section.name }}.
<br><br>
On your  {{ article.section.name }} web page you will find both a text area and a tool to upload the annotated pdf files. Please choose either or both tools to send your answers and any request for corrections back to us.
<br>
Should you decide to use the text area, please explain very clearly where changes should occur referring to the typeset version (page number, paragraph and line, or equation number), and specify both the old (wrong) version and the correction.
<br>
The corrected version will not be sent to you again.
<br><br>
Thank you and best regards,
<br><br>
{{ article.journal.code }} Typesetter
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            proofreading_request_setting,
            proofreading_request_setting_value,
            proofreading_request_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def editor_deassign_reviewer_messages() -> tuple[SettingValue, ...]:
        subject_editor_deassign_reviewer: SettingParams = {
            "name": "editor_deassign_reviewer_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject of reviewer deassigned notification."),
            "description": _(
                "The subject of the notification that is sent to the reviewer when deassigned.",
            ),
            "is_translatable": False,
        }
        subject_editor_deassign_reviewer_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Invite to review withdrawn",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_editor_deassign_reviewer,
            subject_editor_deassign_reviewer_setting_value,
            subject_editor_deassign_reviewer["name"],
            force=force,
        )
        editor_deassign_reviewer_setting: SettingParams = {
            "name": "editor_deassign_reviewer_default",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Defalt body of reviewer deassign notification."),
            "description": _(
                "The default body of the notification that is sent to the reviewer when deassigned. This can be modified by the operator.",
            ),
            "is_translatable": False,
        }
        editor_deassign_reviewer_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear colleague,
<br><br>
This is to inform you that the editor in charge of the {{ article.section.name }} "{{ article.title }}" has been able to make a decision thereby relieving you of the assignment.
<br><br>
{{ article.journal.code }} looks forward to having another opportunity to avail itself of your expertise in the future.
<br><br>
Thank you and best regards,
<br><br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            editor_deassign_reviewer_setting,
            editor_deassign_reviewer_setting_value,
            editor_deassign_reviewer_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def eo_opens_appeal_message():
        subject_eo_opens_appeal: SettingParams = {
            "name": "eo_opens_appeal_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for EO opening an appeal."),
            "description": _(
                "The subject of the notification that is sent to the author when EO opens an appeal.",
            ),
            "is_translatable": False,
        }
        subject_eo_opens_appeal_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Appeal granted",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_eo_opens_appeal,
            subject_eo_opens_appeal_setting_value,
            subject_eo_opens_appeal["name"],
            force=force,
        )
        eo_opens_appeal_setting: SettingParams = {
            "name": "eo_opens_appeal_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for EO opening an appeal."),
            "description": _(
                "The body of the notification that is sent to the author when EO opens an appeal.",
            ),
            "is_translatable": False,
        }
        eo_opens_appeal_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ article.correspondence_author.full_name }},
<br><br>
you have been enabled to submit your appeal against rejection.
Please [...] do so within 30 days, otherwise the procedure of appeal will be closed
and rejection will stand.
<br><br>
Thank you and best regards,
<br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            eo_opens_appeal_setting,
            eo_opens_appeal_setting_value,
            eo_opens_appeal_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def author_withdraws_preprint_message():
        subject_author_withdraws_preprint: SettingParams = {
            "name": "author_withdraws_preprint_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for author withdrawing a preprint."),
            "description": _(
                "The subject of the notification that is sent to the EO/Editor when a preprint is withdrawn.",
            ),
            "is_translatable": False,
        }
        subject_author_withdraws_preprint_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Withdrawn",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_author_withdraws_preprint,
            subject_author_withdraws_preprint_setting_value,
            subject_author_withdraws_preprint["name"],
            force=force,
        )
        author_withdraws_preprint_setting: SettingParams = {
            "name": "author_withdraws_preprint_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for author withdrawing a preprint."),
            "description": _(
                "The body of the notification that is sent to the EO/Editor when a preprint is withdrawn. The author can modify it (so don't include the editor's name).",
            ),
            "is_translatable": False,
        }
        author_withdraws_preprint_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear Editor, <br><br>
This is to inform you that I have just withdrawn my {{ article.section.name }} from {{ article.journal.code }}.
<br>
<br>
Thank you and best regards,
<br><br>
{{ article.correspondence_author.full_name }}
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            author_withdraws_preprint_setting,
            author_withdraws_preprint_setting_value,
            author_withdraws_preprint_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def preprint_withdrawn_system_message():
        subject_preprint_withdrawn_preprint: SettingParams = {
            "name": "preprint_withdrawn_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for author withdrawing a preprint."),
            "description": _(
                "The subject of the notification that is sent to the reviewers/typesetter when a preprint is withdrawn.",
            ),
            "is_translatable": False,
        }
        subject_preprint_withdrawn_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Withdrawn",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_preprint_withdrawn_preprint,
            subject_preprint_withdrawn_setting_value,
            subject_preprint_withdrawn_preprint["name"],
            force=force,
        )
        preprint_withdrawn_setting: SettingParams = {
            "name": "preprint_withdrawn_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for author withdrawing a preprint."),
            "description": _(
                "The body of the notification that is sent to the reviewers/typesetter when a preprint is withdrawn.",
            ),
            "is_translatable": False,
        }
        preprint_withdrawn_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ recipient.full_name }},
<br><br>
This manuscript has been withdrawn from {{ article.journal.code }} and
does not need to be handled by you anymore.
<br>
{{ article.journal.code }} thanks you very much for your collaboration.
<br>
Best regards,
<br><br>
{{ article.journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            preprint_withdrawn_setting,
            preprint_withdrawn_setting_value,
            preprint_withdrawn_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def author_submits_appeal_message():
        subject_author_submits_appeal: SettingParams = {
            "name": "author_submits_appeal_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for author submitting an appeal."),
            "description": _(
                "The subject of the notification that is sent to the editor when author submits an appeal.",
            ),
            "is_translatable": False,
        }
        subject_author_submits_appeal_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Appeal submitted",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_author_submits_appeal,
            subject_author_submits_appeal_setting_value,
            subject_author_submits_appeal["name"],
            force=force,
        )
        author_submits_appeal_setting: SettingParams = {
            "name": "author_submits_appeal_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for when author submits an appeal."),
            "description": _(
                "The body of the notification that is sent to the editor when author submits an appeal.",
            ),
            "is_translatable": False,
        }
        author_submits_appeal_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ editor.full_name }},
<br><br>
The author of the {{ article.section.name }} "{{ article.title }}" has appealed against rejection.
<br>
Please connect to the manuscript <a href="{{ article.articleworkflow.url }}">web page</a>
and kindly handle the appeal within 5 days.
<br><br>
Thank you and best regards,
<br>
{{ journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            author_submits_appeal_setting,
            author_submits_appeal_setting_value,
            author_submits_appeal_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def eo_send_back_to_typesetting_message():
        subject_eo_send_back_to_typesetting: SettingParams = {
            "name": "eo_send_back_to_typesetting_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject for EO send the paper back to the typesetter."),
            "description": _(
                "The subject of the notification that is sent to the typesetter when EO send the paper back.",
            ),
            "is_translatable": False,
        }
        subject_eo_send_back_to_typesetting_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Sent back to typesetter by EO",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            subject_eo_send_back_to_typesetting,
            subject_eo_send_back_to_typesetting_setting_value,
            subject_eo_send_back_to_typesetting["name"],
            force=force,
        )
        eo_send_back_to_typesetting_setting: SettingParams = {
            "name": "eo_send_back_to_typesetting_body",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Default message for EO send the paper back to the typesetter."),
            "description": _(
                "The body of the notification that is sent to the typesetter when EO send the paper back.",
            ),
            "is_translatable": False,
        }
        # Setting is written in italian because it's the language used between EO and typesetters
        eo_send_back_to_typesetting_setting_value: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Ciao {{ typesetter.first_name }},
<br><br>
ti rimando il paper {{ article.articleworkflow }} per
<br>
...inserire motivo...
<br><br>
Grazie,
<br>
{{ user.first_name }}
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            eo_send_back_to_typesetting_setting,
            eo_send_back_to_typesetting_setting_value,
            eo_send_back_to_typesetting_setting["name"],
            force=force,
        )
        return setting_1, setting_2

    def technicalrevisions_complete_reviewer_notification() -> tuple[SettingValue, ...]:
        setting_parms: SettingParams = {
            "name": "technicalrevisions_complete_reviewer_notification_subject",
            "group": wjs_review_settings_group,
            "types": "text",
            "pretty_name": _("Subject of author submits technical revision for reviewers"),
            "description": _(
                "Subject of the notification sent to reviewers when an author updates metadata (i.e. submits a technical revision).",
            ),
            "is_translatable": False,
        }
        value_params: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": "Metadata updated",
            "translations": {},
        }
        setting_1 = create_customization_setting(
            setting_parms,
            value_params,
            setting_parms["name"],
            force=force,
        )
        setting_parms: SettingParams = {
            "name": "technicalrevisions_complete_reviewer_notification_body",
            "group": wjs_review_settings_group,
            "types": "rich-text",
            "pretty_name": _("Body of author submits technical revision for reviewers"),
            "description": _(
                "Body of the notification sent to reviewers when an author updates metadata (i.e. submits a technical revision)."
            ),
            "is_translatable": False,
        }
        value_params: SettingValueParams = {
            "journal": None,
            "setting": None,
            "value": """Dear {{ reviewer.full_name }},
<br><br>
The author has just updated metadata for {{ article.section.name }} "{{ article.title }}". The change(s) is/are visible
on the web pages only. If either the title and/or the abstract have been changed, the pdf file will be updated either in
a revised version (if requested) or during the stage of proofreading (in case of acceptance for publication).
<br><br>
Thank you and best regards,
<br>
{{ journal.code }} Journal
""",
            "translations": {},
        }
        setting_2 = create_customization_setting(
            setting_parms,
            value_params,
            setting_parms["name"],
            force=force,
        )
        return setting_1, setting_2

    with export_to_csv_manager("wjs_review") as csv_writer:
        csv_writer.write_settings(acceptance_due_date())
        csv_writer.write_settings(review_lists_page_size())
        csv_writer.write_settings(declined_review_notice())
        csv_writer.write_settings(core_review_settings())
        csv_writer.write_settings(review_decision_not_suitable_message())
        csv_writer.write_settings(revision_request_postpone_date_due_messages())
        csv_writer.write_settings(technical_revision_body())
        csv_writer.write_settings(author_can_contact_director())
        csv_writer.write_settings(hijack_notification_message())
        csv_writer.write_settings(admin_deems_unimportant())
        csv_writer.write_settings(admin_requires_resubmission())
        csv_writer.write_settings(prophy_settings())
        csv_writer.write_settings(due_date_postpone_message())
        csv_writer.write_settings(due_date_far_future_message())
        csv_writer.write_settings(editor_decline_assignment_messages())
        csv_writer.write_settings(editor_assigns_themselves_as_reviewer_message())
        csv_writer.write_settings(article_requires_proofreading_message())
        csv_writer.write_settings(eo_is_assigned_message())
        csv_writer.write_settings(editor_deassign_reviewer_messages())
        csv_writer.write_settings(eo_opens_appeal_message())
        csv_writer.write_settings(author_withdraws_preprint_message())
        csv_writer.write_settings(preprint_withdrawn_system_message())
        csv_writer.write_settings(author_submits_appeal_message())
        csv_writer.write_settings(eo_send_back_to_typesetting_message())
        csv_writer.write_settings(technicalrevisions_complete_reviewer_notification())


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
