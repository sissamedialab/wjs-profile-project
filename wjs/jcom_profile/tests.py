"""Tests (first attempt)."""

import pytest
from django.test import TestCase
from core.models import Account
from django.core.exceptions import ObjectDoesNotExist
from wjs.jcom_profile.models import JCOMProfile
from django.test.client import RequestFactory
from journal.tests.utils import make_test_journal
from press.models import Press
from django.test import Client


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

    def tearDown(self):
        """Clean up my mess (remove the test journal)."""
        # I wanted to inspect the DB _before_ teardown,
        # but the following does not work. I guess because pytest
        # "swallows" the `set_trace()` in same magic way...
        # import ipdb; ipdb.set_trace()
        # self.press.delete()

    def create_journal(self):
        """Create a press/journal and set the graphical theme."""
        # copied from journal.tests.test_models
        self.request_factory = RequestFactory()
        self.press = Press(domain="sitetestpress.org")
        self.press.save()
        self.request_factory
        journal_kwargs = dict(
            code=self.journal_code,
            domain="sitetest.org",
            # journal_theme='JCOM',  # No!
        )
        self.journal = make_test_journal(**journal_kwargs)

    @pytest.mark.skip(reason="Package installed as app (not as plugin).")
    def test_registerURL_points_to_plugin(self):
        """The "register" link points to the plugin's registration form."""
        client = Client()
        journal_path = f"/{self.journal_code}/"
        response = client.get(journal_path)
        expected_register_link = \
            f'/{self.journal_code}/plugins/register/step/1/"> Register'
        #                          ^^^^^^^
        # Attenzione allo spazio prima di "Register"!
        # In the case of an app, use the following:
        #    f'/{self.journal_code}/register/step/1/"> Register'
        #                          ^_ no "/plugins" path
        self.assertContains(response, expected_register_link)

    # I wanted to inspect the DB _before_ teardown,
    # but the following does not work. When pytest's debug starts,
    # teardown has already been called.
    # def test_break(self):
    #     """Debug."""
    #     self.assertFalse(True)

    # @pytest.mark.django_db(transaction=True)
    @pytest.mark.django_db
    def test_registrationForm_has_fieldProfession(self):
        """The field "profession" must appear in the registration form.

        The journal must use the JCOM graphical theme.
        """
        # Set graphical theme
        from core.models import Setting
        from utils import setting_handler
        theme = 'JCOM'
        theme_setting = Setting.objects.get(name='journal_theme')
        setting_handler.save_setting(
            theme_setting.group.name,
            theme_setting.name,
            self.journal,
            theme)

        client = Client()
        response = client.get(f"/{self.journal_code}/register/step/1/")
        fragments = [
            '<select name="profession" class="validate" required '
            'id="id_profession">',
            #
            '<label for="id_profession" data-error="" data-success="" '
            'id="label_profession">Profession</label>',
        ]
        for fragment in fragments:
            self.assertContains(response, fragment)

        # import psycopg2
        # with psycopg2.connect(dbname='test_j2',
        #                       user='janeway',
        #                       password='pass',
        #                       host='localhost') as connection:
        #     cursor = connection.cursor()
        #     x = cursor.execute("select * from press_press")
        #     connection.commit()
        #     y = cursor.execute("select * from journal_journal")
        #     connection.commit()
        #     self.assertFalse(True)


    # def test_dummy(self):
    #     import sys
    #     sys.stdin.read(1)
    #     self.assertFalse(True)

class TestJCOMProve:
    """Prove di DB maintenance."""

    # def test_aaa(self):
    #     """AAA."""
    #     Account.objects.create(username="AAA",
    #                            first_name="Afirst",
    #                            last_name="Alast")

    @pytest.mark.django_db
    def test_bbb(self):
        """BBB."""
        Account.objects.create(username="BBB",
                               first_name="Bfirst",
                               last_name="Blast")
        import sys
        sys.stdin.readline()
        assert(False)


    @pytest.mark.django_db(transaction=True)
    def test_ccc(self):
        """CCC."""
        Account.objects.create(username="CCC",
                               first_name="Cfirst",
                               last_name="Clast")
        import sys
        sys.stdin.readline()
        assert(False)
