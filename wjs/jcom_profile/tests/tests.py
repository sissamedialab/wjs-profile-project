"""Tests (first attempt)."""

import pytest
from core.models import Account, Setting
from django.test import Client
from utils import setting_handler

from wjs.jcom_profile.forms import JCOMProfileForm, JCOMRegistrationForm
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.tests.conftest import (
    GDPR_FRAGMENTS_JOURNAL,
    JOURNAL_CODE,
    PROFESSION_SELECT_FRAGMENTS_JOURNAL,
    PROFESSION_SELECT_FRAGMENTS_PRESS,
    USERNAME,
)


class TestJCOMProfileProfessionModelTests:
    @pytest.mark.django_db
    def test_new_account_has_profession_but_it_is_not_set(self, user):
        """A newly created account must have a profession associated.
        However, the profession is not set by default.
        """
        again = Account.objects.get(username=USERNAME)
        assert again.username == USERNAME
        assert again.jcomprofile.profession is None

    @pytest.mark.django_db
    def test_account_can_save_profession(self, user):
        """One can set and save a profession onto an account."""
        profession_id = 2
        jcom_profile = JCOMProfile(janeway_account=user)
        jcom_profile.profession = profession_id
        jcom_profile.save()

        user.accountprofession = jcom_profile
        user.save()

        again = Account.objects.get(username=USERNAME)
        assert again.username == USERNAME
        assert again.jcomprofile.profession == profession_id


# TODO: test that django admin interface has an inline with the
# profile extension. Do I really care?


class TestJCOMProfileURLs:
    @pytest.mark.skip(reason="Package installed as app (not as plugin).")
    def test_register_url_points_to_plugin(self, journal, clear_script_prefix_fix):
        """The "register" link points to the plugin's registration form."""
        client = Client()
        journal_path = f"/{JOURNAL_CODE}/"
        response = client.get(journal_path)
        expected_register_link = f'/{JOURNAL_CODE}/plugins/register/step/1/"> Register'
        assert expected_register_link in response.content.decode()

    @pytest.mark.parametrize("theme,fragments", PROFESSION_SELECT_FRAGMENTS_JOURNAL)
    @pytest.mark.django_db
    def test_journalregistration_form_has_field_profession(self, journal, theme, fragments, clear_script_prefix_fix):
        """The field "profession" must appear in the journal registration form."""
        # Set graphical theme.
        # Do not use `journal.theme`: it has been deprecated!
        theme_setting = Setting.objects.get(name="journal_theme")
        setting_handler.save_setting(theme_setting.group.name, theme_setting.name, journal, theme)

        client = Client()
        response = client.get(f"/{JOURNAL_CODE}/register/step/1/")
        for fragment in fragments:
            assert fragment in response.content.decode()

    @pytest.mark.parametrize("theme,fragments", GDPR_FRAGMENTS_JOURNAL)
    @pytest.mark.django_db
    def test_journal_registration_form_has_gdpr_checkbox(self, journal, theme, fragments, clear_script_prefix_fix):
        theme_setting = Setting.objects.get(name="journal_theme")
        setting_handler.save_setting(theme_setting.group.name, theme_setting.name, journal, theme)

        client = Client()
        response = client.get(f"/{JOURNAL_CODE}/register/step/1/")
        for fragment in fragments:
            assert fragment in response.content.decode()

    @pytest.mark.parametrize("theme,fragments", PROFESSION_SELECT_FRAGMENTS_PRESS)
    @pytest.mark.django_db
    def test_pressregistration_form_has_field_profession(self, press, theme, fragments):
        """The field "profession" must appear in the press registration form."""
        # Set graphical theme
        press.theme = theme
        press.save()

        client = Client()
        response = client.get("/register/step/1/")
        for fragment in fragments:
            assert fragment in response.content.decode()

    @pytest.mark.parametrize("theme,fragments", GDPR_FRAGMENTS_JOURNAL)
    @pytest.mark.django_db
    def test_press_registration_form_has_gdpr_checkbox(self, journal, theme, fragments, clear_script_prefix_fix):
        theme_setting = Setting.objects.get(name="journal_theme")
        setting_handler.save_setting(theme_setting.group.name, theme_setting.name, journal, theme)

        client = Client()
        response = client.get("/register/step/1/")
        for fragment in fragments:
            assert fragment in response.content.decode()


class TestJCOMWIP:
    """Tests in `pytest`-style."""

    def test_registration_form_field_profession_is_mandatory(self):
        """The field "profession" is mandatory in the registration form."""
        form = JCOMRegistrationForm()
        assert form.fields.get("profession").required

    def test_gdpr_checkbox_is_mandatory(self):
        """The field "profession" is mandatory in the registration form."""
        form = JCOMRegistrationForm()
        assert form.fields.get("gdpr_checkbox").required

    def test_profile_form_field_profession_is_mandatory(self):
        """The field "profession" is mandatory in the profile form."""
        form = JCOMProfileForm()
        assert form.fields.get("profession").required

    @pytest.mark.django_db
    def test_field_profession_label(self, user):
        """The label of field "profession" must be "profession"."""
        # https://developer.mozilla.org/en-US/docs/Learn/Server-side/Django/Testing#models
        # TODO: what about translations?
        # TODO: what about Uppercase?
        profile = JCOMProfile.objects.get(id=user.id)
        field_label = profile._meta.get_field("profession").verbose_name
        expected_label = "profession"
        assert field_label == expected_label
