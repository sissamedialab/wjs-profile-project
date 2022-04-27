"""The model for a field "profession" for JCOM authors."""

from django.db import models
from core.models import Account
from core.models import AccountManager
# TODO: use settings.AUTH_USER_MODEL
# from django.conf import settings

PROFESSIONS = (
    (0, 'A researcher in S&T studies,'
     ' science communication or neighbouring field'),
    (1, 'A practitioner in S&T'
     ' (e.g. journalist, museum staff, writer, ...)'),
    (2, 'An active scientist'),
    (3, 'Other'),
)


class JCOMProfile(Account):
    """An enrichment of Janeway's Account."""

    objects = AccountManager()

    profession = models.IntegerField(
        null=False,
        # If there is no "default", when an Account is created, a
        # profession must be provided. Otherwise the
        # account.accountprofession results missing.
        # default=3,
        choices=PROFESSIONS)
