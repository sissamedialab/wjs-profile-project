"""Tests (first attempt)."""

import lxml.html
import pytest
from core.models import Account
from django.test import Client
from journal.models import Journal

from wjs.jcom_profile.forms import JCOMRegistrationForm
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.profile.forms import WjsPersonalInfoForm
from wjs.jcom_profile.templatetags.wjs_tags import is_field_available
from wjs.jcom_profile.tests.conftest import EXTRAFIELDS_XPATHS


class TestJCOMProfileProfessionModelTests:
    @pytest.mark.django_db
    def test_new_account_has_profession_but_it_is_not_set(self, user):
        """A newly created account must have a profession associated.

        However, the profession is not set by default.
        """
        again = Account.objects.get(username=user.username)
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

        again = Account.objects.get(username=user.username)
        assert again.jcomprofile.profession == profession_id


# TODO: test that django admin interface has an inline with the
# profile extension. Do I really care?


class TestJCOMProfileSaveTests:
    @pytest.mark.django_db
    def test_jcomprofile_save_is_admin_flag(self, user):
        """A newly created account has is_admin False.

        JCOMprofile save() sets is_admin = is_superuser.
        """

        account_jp = JCOMProfile.objects.get(username=user.username)
        account_jp.is_superuser = True
        assert not account_jp.is_admin
        account_jp.save()
        assert account_jp.is_admin


class TestJCOMProfileURLs:
    @pytest.mark.skip(reason="Package installed as app (not as plugin).")
    def test_register_url_points_to_plugin(self, journal):
        """The "register" link points to the plugin's registration form."""
        client = Client()
        journal_path = f"/{journal.code}/"
        response = client.get(journal_path)
        expected_register_link = f'/{journal.code}/plugins/register/step/1/"> Register'
        assert expected_register_link in response.content.decode()

    @pytest.mark.parametrize("xpath", EXTRAFIELDS_XPATHS)
    @pytest.mark.django_db
    def test_journal_registration_form_has_extrafields(self, journal, xpath):
        """The extra fields must appear in the **journal** registration form."""
        client = Client()
        response = client.get(f"/{journal.code}/register/step/1/")
        html = lxml.html.fromstring(response.content.decode())
        assert html.xpath(xpath)

    @pytest.mark.xfail
    @pytest.mark.parametrize("xpath", EXTRAFIELDS_XPATHS)
    @pytest.mark.django_db
    def test_journal_user_profile_form_has_extrafields(self, admin, journal, xpath):
        """The extra fields must appear in the **journal** user profile form."""
        client = Client()
        client.force_login(admin)
        response = client.get(f"/{journal.code}/profile/")

        assert response.status_code == 200
        html = lxml.html.fromstring(response.content.decode())
        assert html.xpath(xpath)

    @pytest.mark.xfail
    @pytest.mark.parametrize("xpath", EXTRAFIELDS_XPATHS)
    @pytest.mark.django_db
    def test_press_user_profile_form_has_extrafields(self, admin, press, xpath):
        """The extra fields must appear in the **press** user profile form."""
        # The press "theme" is managed by INSTALLATION_BASE_THEME.
        client = Client()
        client.force_login(admin)
        response = client.get("/profile/")
        html = lxml.html.fromstring(response.content.decode())
        assert html.xpath(xpath)


class TestJCOMWIP:
    """Tests in `pytest`-style."""

    @pytest.mark.django_db
    def test_registration_form_field_profession_is_mandatory(self, journal: Journal):
        """The field "profession" is mandatory in the registration form."""
        form = JCOMRegistrationForm(journal=journal)
        assert form.fields.get("profession").required

    @pytest.mark.django_db
    def test_gdpr_checkbox_is_mandatory(self, journal: Journal):
        """The field "profession" is mandatory in the registration form."""
        form = JCOMRegistrationForm(journal=journal)
        assert form.fields.get("gdpr_checkbox").required

    @pytest.mark.parametrize("profession", (True, False))
    @pytest.mark.django_db
    def test_profile_form_field_profession_is_mandatory(self, profession, settings, user: Account, journal: Journal):
        """The field "profession" is mandatory in the profile form."""
        if not profession:
            settings.PROFILE_FIELDS[journal.code] = (
                "first_name",
                "middle_name",
                "last_name",
                "year_of_birth",
            )
        else:
            settings.PROFILE_FIELDS[journal.code] = (
                "first_name",
                "middle_name",
                "last_name",
                "year_of_birth",
                "profession",
            )
        form = JCOMRegistrationForm(journal=journal)
        assert form.fields.get("profession").required == is_field_available(journal, "profession")
        form = WjsPersonalInfoForm(journal=journal, instance=user)
        assert form.fields.get("profession").required == is_field_available(journal, "profession")

    @pytest.mark.parametrize("profession", (True, False))
    @pytest.mark.django_db
    def test_profile_form_field_career_stage_is_mandatory(self, profession, settings, user: Account, journal: Journal):
        """The field "career_stage" is mandatory in the profile form if enabled."""
        if not profession:
            settings.PROFILE_FIELDS[journal.code] = (
                "first_name",
                "middle_name",
                "last_name",
                "year_of_birth",
            )
        else:
            settings.PROFILE_FIELDS[journal.code] = (
                "first_name",
                "middle_name",
                "last_name",
                "year_of_birth",
                "career_stage",
            )
        form = JCOMRegistrationForm(journal=journal)
        assert form.fields.get("career_stage").required == is_field_available(journal, "career_stage")
        form = WjsPersonalInfoForm(journal=journal, instance=user)
        assert form.fields.get("career_stage").required == is_field_available(journal, "career_stage")

    @pytest.mark.django_db
    def test_field_profession_label(self, user):
        """The label of field "profession" must be "profession"."""
        # https://developer.mozilla.org/en-US/docs/Learn/Server-side/Django/Testing#models
        # TODO: what about translations?
        # TODO: what about Uppercase?
        profile = JCOMProfile.objects.get(pk=user.pk)
        field_label = profile._meta.get_field("profession").verbose_name
        expected_label = "profession"
        assert field_label == expected_label
