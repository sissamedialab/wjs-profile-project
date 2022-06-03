"""Tests (first attempt)."""

from django.test import TestCase
from core.models import Account
from django.core.exceptions import ObjectDoesNotExist
from wjs.jcom_profile.models import JCOMProfile
# from utils.testing import helpers


class JCOMProfileProfessionModelTests(TestCase):

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
        self.assertIsNone(again.jcomprofile.profession)

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
        jcom_profile = JCOMProfile(janeway_account=userX)
        jcom_profile.profession = profession_id
        jcom_profile.save()

        userX.accountprofession = jcom_profile
        userX.save()

        again = Account.objects.get(username=self.username)
        self.assertEqual(again.username, self.username)
        self.assertEqual(again.jcomprofile.profession, profession_id)


# TODO: test that django admin interface has an inline with the
# profile extension. Do I really care?

class JCOMProfileURLs(TestCase):

    def setUp(self):
        """Prepare a journal with "JCOM" graphical theme."""
        self.journal_code = 'PIPPO'
        self.create_journal()

    def create_journal(self):
        """Create a journal and set the graphical theme."""
        from journal.models import Journal
        self.journal = Journal.objects.create(
            code=self.journal_code,
            description="Journal description - not used",
        )
        # The field "description" in the journal model is not
        # used. Instead, a "journal_description" property is set.
        # See also: https://github.com/BirkbeckCTP/janeway/pull/2903
        # See also: core/templatetags/settings.py
        from core.models import Setting
        from utils import setting_handler
        used_description = Setting.objects.get(name='journal_description')
        setting_handler.save_setting(
            used_description.group.name,
            used_description.name,
            self.journal,
            "Journal description - in settings")

        # Set graphical theme
        theme = 'JCOM'
        theme_setting = Setting.objects.get(name='journal_theme')
        setting_handler.save_setting(
            theme_setting.group.name,
            theme_setting.name,
            self.journal,
            theme)

    def test_registerURL_points_to_plugin(self):
        """The "register" link points to the plugin's registration form."""
        from django.test import Client
        client = Client()
        journal_path = f"/{self.journal_code}/"
        response = client.get(journal_path)
        expected_register_link = \
            f'/{self.journal_code}/plugins/register/step/1/">Register'
        self.assertContains(response, expected_register_link)
