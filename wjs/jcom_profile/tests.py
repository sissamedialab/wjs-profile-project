"""Tests (first attempt)."""

import pytest
from core.models import Account
from django.core.exceptions import ObjectDoesNotExist
from wjs.jcom_profile.models import JCOMProfile
from journal.tests.utils import make_test_journal
from press.models import Press
from django.test import Client

USERNAME = 'userX'


def drop_userX():
    """Delete the test user."""
    try:
        userX = Account.objects.get(username=USERNAME)
    except ObjectDoesNotExist:
        pass
    else:
        userX.delete()


@pytest.fixture
def userX():
    """Create / reset a user in the DB.

    Create both core.models.Account and wjs.jcom_profile.models.JCOMProfile.
    """
    # Delete the user (just in case...).
    drop_userX()

    userX = Account(username=USERNAME,
                    first_name="User", last_name="Ics")
    userX.save()
    yield userX
    drop_userX()


class TestJCOMProfileProfessionModelTests:

    @pytest.mark.django_db(transaction=True)
    def test_new_account_has_profession_but_it_is_not_set(userX):
        """A newly created account must have a profession associated.

        However, the profession is not set by default.
        """
        again = Account.objects.get(username=USERNAME)
        assert again.username == USERNAME
        assert again.jcomprofile.profession is None

    @pytest.mark.django_db
    def test_account_can_save_profession(userX):
        """One can set and save a profession onto an account."""
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

        again = Account.objects.get(username=USERNAME)
        assert again.username == USERNAME
        assert again.jcomprofile.profession == profession_id


# TODO: test that django admin interface has an inline with the
# profile extension. Do I really care?

JOURNAL_CODE = 'PIPPO'


@pytest.fixture
def journalPippo():
    """Prepare a journal with "JCOM" graphical theme."""
    # I need a "press" where the journal can live:
    # (copied from journal.tests.test_models)
    press = Press(domain="sitetestpress.org")
    press.save()
    journal_kwargs = dict(
        code=JOURNAL_CODE,
        domain="sitetest.org",
        # journal_theme='JCOM',  # No!
    )
    journal = make_test_journal(**journal_kwargs)
    yield journal
    press.delete()


class TestJCOMProfileURLs():

    @pytest.mark.skip(reason="Package installed as app (not as plugin).")
    def test_registerURL_points_to_plugin(self, journalPippo):
        """The "register" link points to the plugin's registration form."""
        client = Client()
        journal_path = f"/{JOURNAL_CODE}/"
        response = client.get(journal_path)
        expected_register_link = \
            f'/{JOURNAL_CODE}/plugins/register/step/1/"> Register'
        #                          ^^^^^^^
        # Attenzione allo spazio prima di "Register"!
        # In the case of an app, use the following:
        #    f'/{JOURNAL_CODE}/register/step/1/"> Register'
        #                          ^_ no "/plugins" path
        assert expected_register_link in response

    @pytest.mark.django_db
    def test_registrationForm_has_fieldProfession(self, journalPippo):
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
            journalPippo,
            theme)

        client = Client()
        response = client.get(f"/{JOURNAL_CODE}/register/step/1/")
        fragments = [
            '<select name="profession" class="validate" required '
            'id="id_profession">',
            #
            '<label for="id_profession" data-error="" data-success="" '
            'id="label_profession">Profession</label>',
        ]
        for fragment in fragments:
            assert fragment in response


class TestJCOMWIP:
    """Tests in `pytest`-style."""

    @pytest.mark.skip(reason="WRITE ME!")
    @pytest.mark.django_db
    def test_fieldProfession_isMandatory(self):
        """The field "profession" is mandatory in the registration form."""
        assert False, "WRITE ME!"

    @pytest.mark.django_db
    def test_fieldProfession_label(self, userX):
        """The label of field "profession" must be "profession"."""
        # TODO: what about translations?
        # TODO: what about Uppercase?
        profile = JCOMProfile.objects.get(id=userX.id)
        field_label = profile._meta.get_field('profession').verbose_name
        expected_label = "profession"
        assert field_label == expected_label
