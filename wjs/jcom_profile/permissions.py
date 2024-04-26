from core.models import AccountRole
from django.contrib.auth import get_user_model
from journal.models import Journal

from . import constants

Account = get_user_model()


def has_eo_role(user: Account) -> bool:
    """
    Check if the given user is part of the EO group.

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user belongs to the EO group, False otherwise.
    :rtype: bool
    """
    return user.groups.filter(name=constants.EO_GROUP).exists()


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


def hijack_eo_and_admins_only(*, hijacker: Account, hijacked: Account) -> bool:
    """
    Check hijack permissions: Superusers and EO members may hijack other staff and regular users, but not superusers.
    """
    if not hijacked.is_active or hijacked.is_superuser:
        return False

    if hijacker.is_superuser or has_eo_role(hijacker):
        return True

    return False


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
