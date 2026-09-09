"""
Tests for the jcom/jcap correspondence-author validators.

These validators were moved here from wjs-submission's account_validation.py because they need
JCOMProfile fields (profession, biography, records_*), which wjs-submission must not depend on
(see wjs-submission-project#30). wjs-submission's own default_correspondence_author_validation
is tested in that repo's tests/test_account_validation.py.
"""

import pytest
from core.models import Account
from django.conf import settings
from django.utils.module_loading import import_string

from wjs.jcom_profile.account_validation import (
    jcap_correspondence_author_validation,
    jcom_correspondence_author_validation,
)
from wjs.jcom_profile.constants import PROFESSIONS
from wjs.jcom_profile.models import JCOMProfile


def _reload(user: Account) -> Account:
    """Return a fresh Account instance with no cached related objects."""
    return Account.objects.get(pk=user.pk)


def _make_jcom_valid(user: Account) -> Account:
    """Set every jcom_correspondence_author_validation requirement to a valid value."""
    Account.objects.filter(pk=user.pk).update(biography="A short bio.")
    JCOMProfile.objects.filter(pk=user.pk).update(
        profession=PROFESSIONS[0][0],
        records_scix="https://scixplorer.org/author/doe",
        records_inspire="",
        records_arxiv="",
        records_other="",
    )
    return _reload(user)


def _make_jcap_valid(user: Account) -> Account:
    """Set every jcap_correspondence_author_validation requirement to a valid value."""
    JCOMProfile.objects.filter(pk=user.pk).update(
        records_scix="https://scixplorer.org/author/doe",
        records_inspire="",
        records_arxiv="",
        records_other="",
    )
    return _reload(user)


# -- jcom_correspondence_author_validation -----------------------------------------------------


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_valid_user_passes(user: Account):
    valid_user = _make_jcom_valid(user)
    assert jcom_correspondence_author_validation(valid_user), "a fully-completed jcom profile should validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_inactive_user_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(is_active=False)
    assert not jcom_correspondence_author_validation(_reload(valid_user)), "an inactive user must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_missing_biography_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(biography="")
    assert not jcom_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without a biography must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_missing_profession_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=None)
    assert not jcom_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without a profession must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_out_of_choices_profession_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=-1)
    assert not jcom_correspondence_author_validation(
        _reload(valid_user),
    ), "a profession outside PROFESSIONS must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_missing_names_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(first_name="", last_name="")
    assert not jcom_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without first_name nor last_name must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_missing_email_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(email="")
    assert not jcom_correspondence_author_validation(_reload(valid_user)), "a user without an email must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_no_professional_links_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(
        records_scix="",
        records_inspire="",
        records_arxiv="",
        records_other="",
    )
    Account.objects.filter(pk=user.pk).update(facebook="", twitter="", linkedin="")
    assert not jcom_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without any record/social link must not validate"


@pytest.mark.django_db
def test_jcom_correspondence_author_validation_social_handle_alone_passes(user: Account):
    """A single social handle is enough professional_data, no records/ URLs needed."""
    valid_user = _make_jcom_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(
        records_scix="",
        records_inspire="",
        records_arxiv="",
        records_other="",
    )
    Account.objects.filter(pk=user.pk).update(facebook="https://facebook.com/doe")
    assert jcom_correspondence_author_validation(
        _reload(valid_user),
    ), "a social handle alone should satisfy professional_data"


# -- jcap_correspondence_author_validation ------------------------------------------------------


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_valid_user_passes(user: Account):
    valid_user = _make_jcap_valid(user)
    assert jcap_correspondence_author_validation(valid_user), "a fully-completed jcap profile should validate"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_inactive_user_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(is_active=False)
    assert not jcap_correspondence_author_validation(_reload(valid_user)), "an inactive user must not validate"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_missing_first_name_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(first_name="")
    assert not jcap_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without a first_name must not validate"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_missing_email_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(email="")
    assert not jcap_correspondence_author_validation(_reload(valid_user)), "a user without an email must not validate"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_missing_affiliation_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    valid_user.affiliations.all().delete()
    assert not jcap_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without an affiliation must not validate"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_no_professional_links_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(
        records_scix="",
        records_inspire="",
        records_arxiv="",
        records_other="",
    )
    Account.objects.filter(pk=user.pk).update(facebook="", twitter="", linkedin="")
    assert not jcap_correspondence_author_validation(
        _reload(valid_user),
    ), "a user without any record/social link must not validate"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_missing_last_name_still_passes(user: Account):
    """Unlike jcom, jcap only requires first_name, not last_name-or-first_name."""
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(last_name="")
    assert jcap_correspondence_author_validation(
        _reload(valid_user),
    ), "jcap must not require last_name when first_name is present"


@pytest.mark.django_db
def test_jcap_correspondence_author_validation_missing_profession_and_biography_still_passes(user: Account):
    """Unlike jcom, jcap does not require a profession nor a biography."""
    valid_user = _make_jcap_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=None)
    Account.objects.filter(pk=user.pk).update(biography="")
    assert jcap_correspondence_author_validation(
        _reload(valid_user),
    ), "jcap must not require a profession nor a biography"


# -- SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION setting ------------------------------


def test_submission_correspondence_author_validation_function_setting_resolves():
    """
    Every dotted path in the override setting must be importable and callable.

    wjs-submission's account_validation.is_user_eligible_for_correspondence_author resolves this
    setting via django.utils.module_loading.import_string at runtime, so a typo here would only
    surface when a JCOM/JCOMAL/JCAP submission is actually validated - catch it here instead.
    """
    override = settings.SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION
    assert None in override, "the override must define a None fallback (it fully replaces the default dict)"
    for journal_code, dotted_path in override.items():
        control_function = import_string(dotted_path)
        assert callable(control_function), f"{dotted_path} (journal {journal_code!r}) must be callable"

    assert (
        import_string(override["JCAP"]) is jcap_correspondence_author_validation
    ), "JCAP must resolve to jcap_correspondence_author_validation"
    for journal_code in ("JCOM", "JCOMAL"):
        assert (
            import_string(override[journal_code]) is jcom_correspondence_author_validation
        ), f"{journal_code} must resolve to jcom_correspondence_author_validation"
