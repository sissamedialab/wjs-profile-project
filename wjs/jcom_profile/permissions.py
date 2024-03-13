from django.contrib.auth import get_user_model
from journal.models import Journal

from .apps import GROUP_EO

Account = get_user_model()


def is_eo(user: Account) -> bool:
    """Return True if the user is in the EO group."""
    return user.groups.filter(name=GROUP_EO).exists()


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
    """Superusers and EO members may hijack other staff and regular users, but not superusers."""
    if not hijacked.is_active or hijacked.is_superuser:
        return False

    if hijacker.is_superuser or is_eo(hijacker):
        return True

    return False
