"""Tests (first attempt)."""

from django.test import TestCase
from core.models import Account
from django.core.exceptions import ObjectDoesNotExist
from wjs.jcome_profile.models import AccountProfession
# from utils.testing import helpers


class AccountProfessionModelTests(TestCase):

    def setUp(self):
        """Do setup."""
        self.username = 'userX'
        self.drop_userX()

    # TODO: check
    # https://docs.djangoproject.com/en/4.0/topics/testing/overview/#rollback-emulation
    def drop_userX(self):
        """
        Remove "userX".

        Because I'm expecting to re-use the same DB for multiple
        tests.
        """
        try:
            userX = Account.objects.get(username=self.username)
        except ObjectDoesNotExist:
            pass
        else:
            userX.delete()

    def test_new_account_has_profession_but_it_is_not_set(self):
        """A newly created account must have a profession associated.

        However, the profession is not set by default.
        """
        self.drop_userX()
        userX = Account(username=self.username,
                        first_name="User", last_name="Ics")
        userX.save()
        again = Account.objects.get(username=self.username)
        self.assertEqual(again.username, self.username)
        self.assertIsNone(again.accountprofession.profession)

    def test_account_can_save_profession(self):
        """One can set and save a profession onto an account."""
        self.drop_userX()
        userX = Account(username=self.username,
                        first_name="User", last_name="Ics")
        userX.save()

        # Not sure if it would be cleaner to
        #    from .models import PROFESSIONS
        #    profession = PROFESSIONS[random.randint(0, len(PROFESSIONS))]
        # (or something similar)
        # I think not...
        profession_id = 2
        profile_extension = AccountProfession(userX)
        profile_extension.profession = profession_id
        profile_extension.save()

        userX.accountprofession = profile_extension
        userX.save()

        again = Account.objects.get(username=self.username)
        self.assertEqual(again.username, self.username)
        self.assertEqual(again.accountprofession.profession, profession_id)


# TODO: test that django admin interface has an inline with the
# profile extension. Do I really care?
