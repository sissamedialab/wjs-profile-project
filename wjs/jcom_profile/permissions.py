from django.contrib.auth import get_user_model

from .apps import GROUP_EO

Account = get_user_model()


def is_eo(user: Account) -> bool:
    """Return True if the user is in the EO group."""
    return user.groups.filter(name=GROUP_EO).exists()
