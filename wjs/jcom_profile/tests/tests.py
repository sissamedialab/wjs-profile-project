"""Tests (first attempt)."""

import pytest
from core.models import Account
from django.core.exceptions import ObjectDoesNotExist
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.forms import JCOMProfileForm, JCOMRegistrationForm
from journal.tests.utils import make_test_journal

# from utils.testing import helpers
from press.models import Press
from django.test import Client
from django.urls import reverse

USERNAME = "userX"


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
    # Delete the test user (just in case...).
    drop_userX()

    userX = Account(username=USERNAME, first_name="User", last_name="Ics")
    userX.save()
    yield userX


class TestJCOMProfileProfessionModelTests:
    @pytest.mark.django_db
    def test_new_account_has_profession_but_it_is_not_set(self, userX):
        """A newly created account must have a profession associated.

        However, the profession is not set by default.
        """
        again = Account.objects.get(username=USERNAME)
        assert again.username == USERNAME
        assert again.jcomprofile.profession is None

    @pytest.mark.django_db
    def test_account_can_save_profession(self, userX):
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

JOURNAL_CODE = "PIPPO"


@pytest.fixture
def press():
    """Prepare a press."""
    # Copied from journal.tests.test_models
    apress = Press.objects.create(domain="testserver", is_secure=False, name="Medialab")
    apress.save()
    yield apress
    apress.delete()


@pytest.fixture
def journalPippo(press):
    """Prepare a journal."""
    # The  graphical theme is set by the single tests.
    journal_kwargs = dict(
        code=JOURNAL_CODE,
        domain="sitetest.org",
        # journal_theme='JCOM',  # No!
    )
    journal = make_test_journal(**journal_kwargs)
    yield journal
    journal.delete()


class TestJCOMProfileURLs:
    @pytest.mark.skip(reason="Package installed as app (not as plugin).")
    def test_registerURL_points_to_plugin(self, journalPippo):
        """The "register" link points to the plugin's registration form."""
        client = Client()
        journal_path = f"/{JOURNAL_CODE}/"
        response = client.get(journal_path)
        expected_register_link = f'/{JOURNAL_CODE}/plugins/register/step/1/"> Register'
        #                          ^^^^^^^
        # Attenzione allo spazio prima di "Register"!
        # In the case of an app, use the following:
        #    f'/{JOURNAL_CODE}/register/step/1/"> Register'
        #                          ^_ no "/plugins" path
        assert expected_register_link in response.content.decode()

    PROFESSION_SELECT_FRAGMENTS_JOURNAL = [
        (
            "clean",
            (
                '<select name="profession" class="form-control" title="" required id="id_profession">',
                '<label class="form-control-label" for="id_profession">Profession</label>',
            ),
        ),
        (
            "material",
            (
                '<select name="profession" class="form-control" title="" required id="id_profession">',
                '<label class="form-control-label" for="id_profession">Profession</label>',
            ),
        ),
        (
            "OLH",
            (
                '<select name="profession" class="form-control" title="" required id="id_profession">',
                '<label class="form-control-label" for="id_profession">Profession</label>',
            ),
        ),
    ]
    PROFESSION_SELECT_FRAGMENTS_PRESS = [
        (
            "clean",
            (
                '<select name="profession" class="form-control" title="" required id="id_profession">',
                '<label class="form-control-label" for="id_profession">Profession</label>',
            ),
        ),
        (
            "material",
            (
                '<select name="profession" class="validate" required id="id_profession">',
                '<label for="id_profession" data-error="" data-success="" id="label_profession">Profession</label>',
            ),
        ),
        (
            "OLH",
            (
                '<select name="profession" required id="id_profession">',
                """<label for="id_profession">
                Profession
                <span class="red">*</span>""",
            ),
        ),
    ]

    @pytest.mark.parametrize("theme,fragments", PROFESSION_SELECT_FRAGMENTS_JOURNAL)
    @pytest.mark.django_db
    def test_journalregistrationForm_has_fieldProfession(
        self, journalPippo, theme, fragments
    ):
        """The field "profession" must appear in the journal registration form."""
        # Set graphical theme.
        # Do not use `journal.theme`: it has been deprecated!
        from core.models import Setting
        from utils import setting_handler

        theme_setting = Setting.objects.get(name="journal_theme")
        setting_handler.save_setting(
            theme_setting.group.name, theme_setting.name, journalPippo, theme
        )

        client = Client()
        response = client.get(f"/{JOURNAL_CODE}/register/step/1/")
        for fragment in fragments:
            assert fragment in response.content.decode()

    # @override_settings(URL_CONFIG="path")
    # @override_settings(URL_CONFIG="domain", CAPTCHA_TYPE=None)
    # @override_settings(DEFAULT_HOST="http://testserver")
    @pytest.mark.parametrize("theme,fragments", PROFESSION_SELECT_FRAGMENTS_PRESS)
    @pytest.mark.django_db
    def test_pressregistrationForm_has_fieldProfession(self, press, theme, fragments):
        """The field "profession" must appear in the press registration form."""
        # Set graphical theme
        press.theme = theme
        press.save()

        client = Client()
        response = client.get(reverse("core_register"))
        for fragment in fragments:
            assert fragment in response.content.decode()


class TestJCOMWIP:
    """Tests in `pytest`-style."""

    def test_registrationForm_fieldProfession_isMandatory(self):
        """The field "profession" is mandatory in the registration form."""
        form = JCOMRegistrationForm()
        assert form.fields.get("profession").required

    def test_profileForm_fieldProfession_isMandatory(self):
        """The field "profession" is mandatory in the profile form."""
        form = JCOMProfileForm()
        assert form.fields.get("profession").required

    @pytest.mark.django_db
    def test_fieldProfession_label(self, userX):
        """The label of field "profession" must be "profession"."""
        # https://developer.mozilla.org/en-US/docs/Learn/Server-side/Django/Testing#models
        # TODO: what about translations?
        # TODO: what about Uppercase?
        profile = JCOMProfile.objects.get(id=userX.id)
        field_label = profile._meta.get_field("profession").verbose_name
        expected_label = "profession"
        assert field_label == expected_label
