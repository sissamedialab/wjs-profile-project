from typing import Optional

from core.models import AccountRole
from django.contrib.auth import get_user_model
from journal.models import Journal

from . import constants

Account = get_user_model()


def main_role(journal: Journal, user: Account) -> str:
    """
    Return the main role of the user.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: The main role of the user.
    :rtype: str
    """
    if has_eo_role(user):
        return constants.EO_GROUP
    elif has_director_role(journal, user):
        # We do have both a "director" and "main director" roles, but they are functionally equivalent
        return constants.DIRECTOR_ROLE
    elif has_typesetter_role_on_any_journal(user):
        return constants.TYPESETTER_ROLE
    elif has_section_editor_role(journal, user):
        return constants.SECTION_EDITOR_ROLE
    elif has_reviewer_role(journal, user):
        return constants.REVIEWER_ROLE
    elif has_author_role(journal, user):
        return constants.AUTHOR_ROLE


def has_eo_role(user: Account) -> bool:
    """
    Check if the given user is part of the EO group.

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user belongs to the EO group, False otherwise.
    :rtype: bool
    """
    return user.groups.filter(name=constants.EO_GROUP).exists()


def has_eo_or_director_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user is part of the EO or has director role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user belongs to the EO or director group, False otherwise.
    :rtype: bool
    """
    return has_eo_role(user=user) or has_director_role(journal=journal, user=user)


def has_any_journal_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has any role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has any role for the journal, False otherwise.
    :rtype: bool
    """
    return len(user.roles_for_journal(journal=journal)) > 0


def can_hijack_user_role(hijacker: Account) -> bool:
    """
    Check if the given user can hijack another user's role.

    Hijacking is allowed for superusers, EO members, and directors.
    """
    from core.middleware import GlobalRequestMiddleware

    request = GlobalRequestMiddleware.get_current_request()
    return hijacker.is_superuser or has_eo_role(hijacker) or has_director_role(request.journal, hijacker)


def hijack_eo_and_admins_only(*, hijacker: Account, hijacked: Account) -> bool:
    """
    Check hijack permissions: Superusers and EO members may hijack other staff and regular users, but not superusers.
    """
    if not hijacked.is_active or hijacked.is_superuser:
        return False

    return can_hijack_user_role(hijacker)


def has_section_editor_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has section editor role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has section editor role for the journal, False otherwise.
    :rtype: bool
    """
    return user.check_role(journal, constants.SECTION_EDITOR_ROLE)


def has_editor_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has editor role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has editor role for the journal, False otherwise.
    :rtype: bool
    """
    return user.check_role(journal, constants.EDITOR_ROLE)


def has_any_editor_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has any editor (section editor, editor) role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has any editor role for the journal, False otherwise.
    :rtype: bool
    """
    return has_section_editor_role(journal, user) or has_editor_role(journal, user)


def has_author_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has author role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has author role for the journal, False otherwise.
    :rtype: bool
    """
    return user.check_role(journal, constants.AUTHOR_ROLE)


def has_director_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has director role for the given journal.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has director role for the journal, False otherwise.
    :rtype: bool
    """
    return user.check_role(journal, constants.DIRECTOR_ROLE)


def has_reviewer_role(journal: Journal, user: Account) -> bool:
    """
    Check if the given user has reviewer role for the given journal.

    We don't check conditions on assignment on any article.

    :param journal: An instance of the Journal class.
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has reviewer role for the journal, False otherwise.
    :rtype: bool
    """
    return user.check_role(journal, constants.REVIEWER_ROLE)


def has_admin_role(journal: Journal, user: Account) -> bool:
    """
    Return True is the user is staff, also meaning EO."""
    return user.is_staff or has_eo_role(user)


def has_typesetter_role_on_any_journal(user: Account) -> bool:
    """
    Check if the user has the typesetters role in any journal.

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the typesetters role in any journal.
    :rtype: bool
    """
    role = "typesetter"
    return AccountRole.objects.filter(
        user=user,
        role__slug=role,
    ).exists()


def get_hijacker() -> Optional[Account]:
    """
    Return the hijacker of the given message.

    Request object is fetch using :py:class:`core.middleware.GlobalRequestMiddleware`.
    """
    from core.middleware import GlobalRequestMiddleware

    request = GlobalRequestMiddleware.get_current_request()
    try:
        # user.is_hijacked is only set if middleware is activated. during the tests it might not be
        # and in general it's safer to handle the case where it's not set
        if request.user.is_hijacked:
            hijack_history = request.session["hijack_history"]
            if hijack_history:
                hijacker_id = hijack_history[-1]
                return Account.objects.get(pk=hijacker_id)
    except AttributeError:
        pass
