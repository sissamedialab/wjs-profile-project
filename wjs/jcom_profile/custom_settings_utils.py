from typing import Optional, TypedDict, Union

from core.models import Setting, SettingGroup, SettingValue
from django.utils.translation import gettext as _
from journal.models import Journal
from utils.logger import get_logger

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


def patch_setting(setting_params: SettingParams, settingvalue_params: SettingValueParams):
    setting = Setting.objects.get(group=setting_params["group"], name=setting_params["name"])
    setting_value = SettingValue.objects.get(journal=None, setting=setting)
    setting_value.journal = settingvalue_params["journal"]
    setting_value.value = settingvalue_params["value"]
    for field, value in settingvalue_params["translations"].items():
        setattr(setting_value, field, value)
    setting_value.save()


# refs https://gitlab.sissamedialab.it/wjs/specs/-/issues/366
def create_customization_setting(
    setting_params: SettingParams,
    settingvalue_params: SettingValueParams,
    name_for_messages: str,
    update=False,
):
    """
    Command to create a Setting, with its SettingValue
    """
    # capitalize() will make the first letter of a string capitalized but all the other letters lowercase
    name_for_messages_capitalized = name_for_messages[0].upper() + name_for_messages[1:]
    setting, setting_created = Setting.objects.get_or_create(**setting_params)
    try:
        SettingValue.objects.get(journal=None, setting=setting)
        logger.warning(f"{name_for_messages_capitalized} already exists. Do nothing.")
    except SettingValue.DoesNotExist:
        translations = settingvalue_params.pop("translations")
        settingvalue_params.update(translations)
        settingvalue_params["setting"] = setting
        SettingValue.objects.create(**settingvalue_params)
        logger.info(f"Successfully created {name_for_messages} setting.")


def add_submission_figures_data_title():
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
        "value": "Figures and Data Files",
        "setting": None,
        "translations": {
            "value_cy": "Ffigurau a Ffeiliau Data",
            "value_de": "Abbildungen und Datenfiles",
            "value_en": "Figures and Data Files",
            "value_fr": "Figures et dossiers de données",
            "value_nl": "Figuren en gegevensbestanden",
        },
    }
    create_customization_setting(setting_params, settingvalue_params, "submission title of figures and data files")


def add_coauthors_submission_email_settings():
    email_settings_group = get_group("email")
    email_subject_settings_group = get_group("email_subject")

    setting_params: SettingParams = {
        "name": "submission_coauthors_acknowledgment",
        "group": email_settings_group,
        "types": "rich-text",
        "pretty_name": _("Submission Coauthors Acknowledgement"),
        "description": _("Email sent to coauthors when they have submitted an article."),
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": 'Dear {{ author.full_name}}, <br><br>Thank you for submitting "{{ article }}" to {{ article.journal }} as coauthor.<br><br> Your work will now be reviewed by an editor and we will be in touch as the peer-review process progresses.<br><br>Regards,<br>',  # noqa: E501
        "translations": {
            "value_en": 'Dear {{ author.full_name}}, <br><br>Thank you for submitting "{{ article }}" to {{ article.journal }} as coauthor.<br><br> Your work will now be reviewed by an editor and we will be in touch as the peer-review process progresses.<br><br>Regards,<br>',  # noqa: E501
        },
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "email for coauthors submission notification",
    )
    setting_params: SettingParams = {
        "name": "subject_submission_coauthors_acknowledgement",
        "group": email_subject_settings_group,
        "types": "text",
        "pretty_name": _("Submission Subject Coauthors Acknowledgement"),
        "description": _("Subject for Email sent to coauthors when they have submitted an article."),
        "is_translatable": True,
    }
    settingvalue_params: SettingValueParams = {
        "journal": None,
        "setting": None,
        "value": "Coauthor - Article Submission",
        "translations": {
            "value_en": "Coauthor - Article Submission",
        },
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "email subject for coauthors submission notification",
    )


def add_user_as_main_author_setting():
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
        "value": "",
        "translations": {},
    }
    create_customization_setting(setting_params, settingvalue_params, "user as main author")


def add_publication_alert_settings():
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
We have received a request to subscribe your email address to JCOM publication alert.
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
JCOM - Journal of Science Communication
</p>
""",
        "translations": {},
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous subscription email body",
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
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous subscription email subject",
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
        "value": """
Hello,
<p>
We have received a request to subscribe your email address to JCOM publication alert.
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
JCOM - Journal of Science Communication
</p>
""",
        "translations": {},
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous reminder email body",
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
        "value": "Your subscription to JCOM publication alert",
        "translations": {},
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert anonymous reminder email subject",
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
        "value": "See current news",
        "translations": {},
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert email intro message",
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
        "value": "Publication alert subscription",
        "translations": {},
    }
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "publication alert email subject",
    )


def add_generic_analytics_code_setting():
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
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "generic analytics tracking code",
    )


# refs specs#640
def add_general_facebook_handle_setting():
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
    create_customization_setting(
        setting_params,
        settingvalue_params,
        "journal's facebook handle",
    )
