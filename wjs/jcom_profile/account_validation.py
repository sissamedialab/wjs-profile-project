"""
Journal-specific correspondence-author validation.

The rules themselves are data: `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` in `wjs/defaults/settings.py`
lists, per journal, the required profile fields and their public labels; `wjs-submission` evaluates them with
`missing_required_fields`. The functions below are the validators registered from `wjs-submission`'s
`SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION` setting rather than imported directly, so `wjs-submission`
itself does not depend on `wjs.jcom_profile`. They are the injection point: a journal whose rules the dict cannot
express points that setting to its own function (same signature). Submission step 4 always lists the fields of
the dict, so such a journal should also override its own entry of the required-fields dict.
"""

from core.models import Account
from journal.models import Journal
from plugins.wjs_submission.account_validation import missing_required_fields


def jcom_correspondence_author_validation(user: Account, journal: Journal) -> bool:
    """
    Validate whether a user meets the JCOM/JCOMAL criteria to be a correspondence author.

    :param user: The user account to validate
    :type user: Account
    :param journal: The journal whose required fields apply
    :type journal: Journal
    :return: True if the user is valid as a correspondence author, False otherwise
    :rtype: bool
    """
    return not missing_required_fields(user, journal)


def jcap_correspondence_author_validation(user: Account, journal: Journal) -> bool:
    """
    Validate whether a user meets the JCAP criteria to be a correspondence author.

    :param user: The user account to validate
    :type user: Account
    :param journal: The journal whose required fields apply
    :type journal: Journal
    :return: True if the user is valid as a correspondence author, False otherwise
    :rtype: bool
    """
    return not missing_required_fields(user, journal)
