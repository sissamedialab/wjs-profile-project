import csv
from contextlib import contextmanager
from typing import Any, Optional, TypedDict, Union

from core.models import Setting, SettingGroup, SettingValue
from django.conf import settings
from django.utils.translation import gettext as _
from journal.models import Journal
from plugins.wjs_review.reminders.settings import ReminderManager, ReminderSetting
from utils.logger import get_logger
from utils.setting_handler import get_setting

logger = get_logger(__name__)


def get_group(name: str) -> SettingGroup:
    """
    Return a SettingGroup instance with the given name, raise SettingGroup.DoesNotExist if it does not exists.

    If one wants to create a new SettingGroup, the exception must be catched and the SettingGroup created.
    """
    try:
        return SettingGroup.objects.get(name=name)
    except SettingGroup.DoesNotExist:
        logger.warning(f"{name} group does not exist.")
        raise


# TypedDict: https://docs.python.org/3/library/typing.html#typing.TypedDict
class SettingParams(TypedDict):
    name: str
    group: SettingGroup
    types: str  # E.g. "rich-text", "text", "boolean",... (TODO: make full list)
    pretty_name: str
    description: str
    is_translatable: bool


class SettingValueParams(TypedDict):
    journal: Optional[Journal]
    setting: Optional[Setting]  # Set automatically by create_customization_setting()
    value: Union[str, int, bool, float]
    # This has to be a dict with keys like "value_en", "value_pt" (or an empty dict)
    translations: dict


class PatchSettingParams(TypedDict):
    name: str
    group: SettingGroup


class PatchSettingValueParams(TypedDict):
    journal: Journal
    value: Union[str, int, bool, float]
    translations: dict


def patch_setting(setting_params: PatchSettingParams, settingvalue_params: PatchSettingValueParams) -> SettingValue:
    setting = Setting.objects.get(group=setting_params["group"], name=setting_params["name"])
    journal = settingvalue_params["journal"]
    try:
        setting_value = SettingValue.objects.get(journal=journal, setting=setting)
    except SettingValue.DoesNotExist:
        setting_value = SettingValue.objects.get(journal=None, setting=setting)
        setting_value.journal = settingvalue_params["journal"]
    setting_value.value = settingvalue_params["value"]
    for field, value in settingvalue_params["translations"].items():
        setattr(setting_value, field, value)
    setting_value.save()

    # If we have been asked to patch the default value but there are overrides,
    # report it:
    if not settingvalue_params["journal"]:
        for override in SettingValue.objects.filter(
            journal__isnull=False,
            setting=setting,
        ):
            logger.warning(f"    - override exists for {override.journal.code} (patch has no effect)")

    return setting_value


# refs https://gitlab.sissamedialab.it/wjs/specs/-/issues/366
def create_customization_setting(
    setting_params: SettingParams,
    settingvalue_params: SettingValueParams,
    name_for_messages: str,
    force=False,
) -> SettingValue:
    """
    Command to create a Setting, with its SettingValue
    """
    # capitalize() will make the first letter of a string capitalized but all the other letters lowercase
    name_for_messages_capitalized = name_for_messages[0].upper() + name_for_messages[1:]
    setting, setting_created = Setting.objects.get_or_create(
        name=setting_params["name"],
        group=setting_params["group"],
        defaults=setting_params,
    )
    try:
        setting = SettingValue.objects.get(journal=None, setting=setting)
        if force:
            if settings.DEBUG:
                # patch the setting itself
                setting.types = setting_params["types"]
                setting.pretty_name = setting_params["pretty_name"]
                setting.description = setting_params["description"]
                setting.is_translatable = setting_params["is_translatable"]
                setting.save()

                # patch the setting's default value
                setting = patch_setting(setting_params, settingvalue_params)
                logger.warning(f"Overwriting {name_for_messages_capitalized} as requested.")
            else:
                logger.warning(
                    f"You are trying to patch {name_for_messages_capitalized} in a production environment. "
                    f"Doing nothing!",
                )
        else:
            logger.warning(f"{name_for_messages_capitalized} already exists. Do nothing.")
    except SettingValue.DoesNotExist:
        translations = settingvalue_params.pop("translations")
        settingvalue_params.update(translations)
        settingvalue_params["setting"] = setting
        setting = SettingValue.objects.create(**settingvalue_params)
        logger.info(f"Successfully created {name_for_messages} setting.")
    return setting


def add_submission_figures_data_title(force: bool = False) -> tuple[SettingValue, ...]:
    styling_settings_group = get_group(name="styling")

    setting_params: SettingParams = {
        "name": "submission_figures_data_title",
        "group": styling_settings_group,
        "types": "rich-text",
        "pretty_name": _("Files Submission - Title of Figures and Data Files Field"),
        "description": _("Displayed on the Files Submission page."),
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "value": "Additional Files",
        "setting": None,
        "translations": {
            "value_cy": "Ffeiliau Ychwanegol",
            "value_de": "Zusätzliche Dateien",
            "value_en": "Additional Files",
            "value_fr": "Fichiers supplémentaires",
            "value_nl": "Extra bestanden",
        },
    }
    return (
        create_customization_setting(
            setting_params,
            settingvalue_params,
            "submission title of figures and data files",
            force=force,
        ),
    )


def add_coauthors_submission_email_settings(force: bool = False) -> tuple[SettingValue, ...]:
    email_settings_group = get_group("email")
    email_subject_settings_group = get_group("email_subject")

    setting_params: SettingParams = {
        "name": "submission_coauthors_acknowledgement_body",
        "group": email_settings_group,
        "types": "rich-text",
        "pretty_name": _("Submission Coauthors Acknowledgement"),
        "description": _("Email sent to coauthors when they have submitted an article."),
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": """Dear {{ author.full_name}}, <br>
<br>
This is to confirm that {{ article.correspondence_author.full_name }} has just submitted
the {{ article.section.name }} "{{ article.title }}" to {{ article.journal }} on your behalf.<br>
<br>
Please update your user profile and acknowledge the privacy notice, if needed,
from <a href="{{ article.journal.site_url }}{% url 'core_edit_profile' %}">here</a>
as your data will be associated to your manuscript if and when it is published.
Your manuscript is available to you <a href="{{ article.articleworkflow.url }}">here</a>.
<br>
<br>
Thank you and best regards,
<br>
{{ journal.code }} Journal
""",
        "translations": {},
    }
    setting_1 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "email for coauthors submission notification",
        force=force,
    )
    setting_params: SettingParams = {
        "name": "submission_coauthors_acknowledgement_subject",
        "group": email_subject_settings_group,
        "types": "text",
        "pretty_name": _("Submission Subject Coauthors Acknowledgement"),
        "description": _("Subject for Email sent to coauthors when they have submitted an article."),
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "Your manuscript has been submitted",
        "translations": {},
    }
    setting_2 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "email subject for coauthors submission notification",
        force=force,
    )
    return setting_1, setting_2


def add_user_as_main_author_setting(force: bool = False) -> tuple[SettingValue, ...]:
    general_settings_group = get_group("general")
    setting_params: SettingParams = {
        "name": "user_automatically_main_author",
        "group": general_settings_group,
        "types": "boolean",
        "pretty_name": _("User automatically as main author"),
        "description": _(
            "If true, the submitting user is set as main author. "
            "To work, the setting 'user_automatically_author' must be on.",
        ),
        "is_translatable": False,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "on",
        "translations": {},
    }
    return (
        create_customization_setting(
            setting_params,
            settingvalue_params,
            "user as main author",
            force=force,
        ),
    )


def add_publication_alert_settings(force: bool = False) -> tuple[SettingValue, ...]:
    email_settings_group = get_group("email")
    setting_params: SettingParams = {
        "name": "publication_alert_subscription_email_body",
        "group": email_settings_group,
        "types": "rich-text",
        "pretty_name": "Body of the email sent when an anonymous user subscribes to publication alert.",
        "description": "Email body",
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": """
Hello,
<p>
We have received a request to subscribe your email address to {{ journal.code }} publication alert.
</p>
<p>
To confirm your email address, activate your subscription and select your topics of interest click on
<a href="{acceptance_url}" target="_blank">this link</a>
</p>
<p>
By clicking the above link you are agreeing to our <a href="https://medialab.sissa.it/en/privacy">privacy policy</a>.
<br>
You can unsubscribe at any time by using the link provided in every publication alert that you will receive.
</p>
<p>
If you did not request to subscribe, you do not need to do anything. If you do not click on the activation link,
you will not be added to our mailing list.
</p>
<p>
Kind regards,
</p>
<p>
{{ journal.code }} - {{ journal.description }}
</p>
""",
        "translations": {},
    }
    setting_1 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous subscription email body",
        force=force,
    )
    setting_params: SettingParams = {
        "name": "publication_alert_subscription_email_subject",
        "group": email_settings_group,
        "types": "text",
        "pretty_name": "Subject of the email sent when an anonymous user subscribes to publication alert.",
        "description": "Email subject",
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "Publication alert subscription",
        "translations": {},
    }
    setting_2 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous subscription email subject",
        force=force,
    )
    setting_params: SettingParams = {
        "name": "publication_alert_reminder_email_body",
        "group": email_settings_group,
        "types": "rich-text",
        "pretty_name": "Body of the email sent when an anon user subscribes to an alert that is already subscribed to",  # noqa: E501
        "description": "Email body",
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": """Hello,
<p>
We have received a request to subscribe your email address to {{ journal.code }} publication alert.
</p>
<p>
Please note that you are already subscribed. If you wish to change your topics of interest use the link below.
</p>
<p>
<a href="{acceptance_url}">Change topics of interest</a>
</p>
<p>
Kind regards,
</p>
<p>
{{ journal.code }} - {{ journal.description }}
</p>
""",
        "translations": {},
    }
    setting_3 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous reminder email body",
        force=force,
    )
    setting_params: SettingParams = {
        "name": "publication_alert_reminder_email_subject",
        "group": email_settings_group,
        "types": "text",
        "pretty_name": "Subject of the email sent when an anon user subscribes to an alert that is already subscribed to",  # noqa: E501
        "description": "Email subject",
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "Your subscription to {{ journal.code }} publication alert",
        "translations": {},
    }
    setting_4 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous reminder email subject",
        force=force,
    )
    setting_params: SettingParams = {
        "name": "publication_alert_email_intro_message",
        "group": email_settings_group,
        "types": "text",
        "pretty_name": "Introduction to the publication alert body.",
        "description": "Email introduction message",
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "",
        "translations": {},
    }
    setting_5 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert email intro message",
        force=force,
    )
    setting_params: SettingParams = {
        "name": "publication_alert_email_subject",
        "group": email_settings_group,
        "types": "text",
        "pretty_name": "Subject of the publication alert email.",
        "description": "Email subject",
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "{{ journal.code }} - New publication",
        "translations": {},
    }
    setting_6 = create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert email subject",
        force=force,
    )
    return setting_1, setting_2, setting_3, setting_4, setting_5, setting_6


def add_generic_analytics_code_setting(force: bool = False) -> tuple[SettingValue, ...]:
    general_settings_group = get_group("general")
    setting_params: SettingParams = {
        "name": "analytics_code",
        "group": general_settings_group,
        "types": "text",
        "pretty_name": _("Analytics tracking code"),
        "description": _(
            "Code added to every page of the journal in order to track visits and analytics."
            " E.g. Google Analitics or Matomo complete tracking code. Not just the site code 🙂",
        ),
        "is_translatable": False,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "",
        "translations": {},
    }
    return (
        create_customization_setting(
            setting_params,
            settingvalue_params,
            "generic analytics tracking code",
            force=force,
        ),
    )


# refs specs#640
def add_general_facebook_handle_setting(force: bool = False) -> tuple[SettingValue, ...]:
    general_settings_group = get_group("general")
    setting_params: SettingParams = {
        "name": "facebook_handle",
        "group": general_settings_group,
        "types": "text",
        "pretty_name": "Facebook Handle",
        "description": "Journal's facebook handle.",
        "is_translatable": False,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "",
        "translations": {},
    }
    return (
        create_customization_setting(
            setting_params,
            settingvalue_params,
            "journal's facebook handle",
            force=force,
        ),
    )


# refs specs#954
def add_submission_settings(journal: Journal, force: bool = False) -> tuple[SettingValue, ...]:
    general_settings_group = get_group("general")
    setting_1 = patch_setting(
        PatchSettingParams(name="submit_select_issue_form_editor_version", group=general_settings_group),
        PatchSettingValueParams(
            journal=journal, value="wjs.jcom_profile.forms.SelectSpecialIssueForm", translations={}
        ),
    )
    setting_2 = patch_setting(
        PatchSettingParams(name="submit_select_issue_form_general_version", group=general_settings_group),
        PatchSettingValueParams(
            journal=journal, value="wjs.jcom_profile.forms.SelectSpecialIssueForm", translations={}
        ),
    )
    setting_3 = patch_setting(
        PatchSettingParams(name="submit_info_form_editor_version", group=general_settings_group),
        PatchSettingValueParams(
            journal=journal, value="wjs.jcom_profile.forms.KeywordSelectionArticleInfoSubmit", translations={}
        ),
    )
    setting_4 = patch_setting(
        PatchSettingParams(name="submit_info_form_general_version", group=general_settings_group),
        PatchSettingValueParams(
            journal=journal, value="wjs.jcom_profile.forms.KeywordSelectionArticleInfoSubmit", translations={}
        ),
    )
    return (setting_1, setting_2, setting_3, setting_4)


class SettingsCSVWrapper:
    settings_fields = [
        "setting name",
        "setting group",
        "usage",
        "value",
        "verbosity",
        "auto mark as read",
        "auto mark as read by eo",
    ]
    names_fields = ["group", "name"]

    def __init__(self, writer: Optional[csv.DictWriter]):
        self.csv_writer = writer
        self.discovered_settings = []
        self.journal = []

    def _get_setting_data(self, setting_value: SettingValue):
        self.discovered_settings.append(
            {"name": setting_value.setting.name, "group": setting_value.setting.group.name}
        )
        return {
            "setting name": setting_value.setting.name,
            "setting group": setting_value.setting.group.name,
            "value": setting_value.processed_value,
            "usage": setting_value.setting.description,
            "verbosity": "",
            "auto mark as read": "",
            "auto mark as read by eo": "",
        }

    def write_settings(self, settings_list: tuple[SettingValue, ...]):
        if self.csv_writer:
            for setting_value in settings_list:
                self.csv_writer.writerow(self._get_setting_data(setting_value))

    def export_settings(self, journal: Journal, settings_list: tuple[dict[str, Any], ...]):
        if self.csv_writer:
            for setting in settings_list:
                try:
                    setting_value = get_setting(
                        setting_group_name=setting["group"], setting_name=setting["name"], journal=journal
                    )
                    self.csv_writer.writerow(self._get_setting_data(setting_value))
                except (SettingValue.DoesNotExist, Setting.DoesNotExist):
                    logger.warning(f"{setting['name']} setting does not exist.")
                    continue


class RemindersCSVWrapper:
    settings_fields = [
        "reminder code",
        "reminder",
        "subject",
        "body",
        "actor",
        "recipient",
        "days_after",
    ]

    def __init__(self, writer: Optional[csv.DictWriter]):
        self.csv_writer = writer
        self.discovered_settings = []
        self.journal = []

    def _get_setting_data(self, reminder_setting: ReminderSetting):
        return {
            "reminder code": reminder_setting.code,
            "reminder": reminder_setting.code.label,
            "subject": reminder_setting.subject,
            "body": reminder_setting.body,
            "actor": reminder_setting.actor,
            "recipient": reminder_setting.recipient,
            "days_after": reminder_setting.days_after,
        }

    def export_reminders(self, journal: Journal):
        if self.csv_writer:
            for reminder_class in ReminderManager.__subclasses__():
                for reminder_setting in reminder_class.reminders.values():
                    self.csv_writer.writerow(self._get_setting_data(reminder_setting))


@contextmanager
def export_to_csv_manager(application):
    """
    Export settings to a CSV file.

    It must be invoked as a context manager in commands and functions that create settings.

    The context manager is only active in DEBUG mode. In production, it does nothing.

    The settings values returned by the function that creates the settings must be processed by the CSVWrapper instance
    using :py:meth:`write_settings` method.

    It creates two files:
    - settings_{application}.csv: contains the list of settings and their values.
    - settings_names_{application}.csv: contains names and groups of each setting, it's meant to be passed to
        `export_settings` command.

    Example:

        with export_to_csv_manager("jcom_profile") as wrapper:
            wrapper.write_settings(add_submission_figures_data_title())

    """

    if settings.DEBUG:
        with open(f"settings_{application}.csv", "w") as f:
            csv_writer = csv.DictWriter(f, fieldnames=SettingsCSVWrapper.settings_fields)
            csv_writer.writeheader()
            wrapper = SettingsCSVWrapper(csv_writer)
            yield wrapper
        with open(f"settings_names_{application}.csv", "w") as f:
            csv_writer = csv.DictWriter(f, fieldnames=SettingsCSVWrapper.names_fields)
            csv_writer.writeheader()
            csv_writer.writerows(wrapper.discovered_settings)
    else:
        wrapper = SettingsCSVWrapper(None)
        yield wrapper


def export_to_csv(application: str, journal: Journal, settings_list: tuple[dict[str, Any], ...]):
    with open(f"settings_{application}.csv", "w") as f:
        csv_writer = csv.DictWriter(f, fieldnames=SettingsCSVWrapper.settings_fields)
        csv_writer.writeheader()
        wrapper = SettingsCSVWrapper(csv_writer)
        wrapper.export_settings(journal, settings_list)


def export_reminders(journal: Journal):
    with open("settings_reminders.csv", "w") as f:
        csv_writer = csv.DictWriter(f, fieldnames=RemindersCSVWrapper.settings_fields)
        csv_writer.writeheader()
        wrapper = RemindersCSVWrapper(csv_writer)
        wrapper.export_reminders(journal)
