"""
Journal-specific correspondence-author validation rules.

These validators are registered from `wjs-submission`'s
`SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION` setting (see
`wjs/defaults/settings.py`) rather than imported directly, so `wjs-submission` itself does not
depend on `wjs.jcom_profile`.
"""

from core.models import Account

from wjs.jcom_profile.constants import PROFESSIONS


def jcom_correspondence_author_validation(user: Account) -> bool:
    """
    Validate whether a user meets the JCOM/JCOMAL criteria to be a correspondence author.

    :param user: The user account to validate
    :type user: Account
    :return: True if the user is valid as a correspondence author, False otherwise
    :rtype: bool
    """
    jcomprofile = user.jcomprofile
    is_active = user.is_active
    valid_professions = {choice[0] for choice in PROFESSIONS}
    personal_data = bool(
        (user.last_name or user.first_name)
        and user.email
        and jcomprofile.profession in valid_professions
        and user.biography,
    )
    professional_data = bool(
        jcomprofile.records_scix
        or jcomprofile.records_inspire
        or jcomprofile.records_arxiv
        or jcomprofile.records_other
        or user.facebook
        or user.twitter
        or user.linkedin
        or jcomprofile.records_other,
    )
    return is_active and personal_data and professional_data


def jcap_correspondence_author_validation(user: Account) -> bool:
    """
    Validate whether a user meets the JCAP criteria to be a correspondence author.

    :param user: The user account to validate
    :type user: Account
    :return: True if the user is valid as a correspondence author, False otherwise
    :rtype: bool
    """
    is_active = user.is_active
    personal_data = user.first_name and user.email
    jcomprofile = user.jcomprofile
    professional_data = user.affiliation() and bool(
        jcomprofile.records_scix
        or jcomprofile.records_inspire
        or jcomprofile.records_arxiv
        or jcomprofile.records_other
        or user.facebook
        or user.twitter
        or user.linkedin
        or jcomprofile.records_other,
    )
    return is_active and personal_data and professional_data
