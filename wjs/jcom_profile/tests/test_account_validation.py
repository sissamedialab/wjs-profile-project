"""
Tests for the jcom/jcap correspondence-author rules.

The rules are data: `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` (this repo's settings) evaluated by
wjs-submission's `missing_required_fields`. The jcom/jcap validation functions stay as injection points.
They need JCOMProfile fields (profession, biography, records_*), which wjs-submission must not depend on
(see wjs-submission-project#30). wjs-submission's own default rules are tested in that repo's
tests/test_account_validation.py.
"""

import itertools
from types import SimpleNamespace

import pytest
from core.models import Account
from django.conf import settings
from django.utils.module_loading import import_string
from plugins.wjs_submission.account_validation import missing_required_fields

from wjs.jcom_profile.account_validation import (
    jcap_correspondence_author_validation,
    jcom_correspondence_author_validation,
)
from wjs.jcom_profile.constants import PROFESSIONAL_LINKS_LABEL, PROFESSIONS
from wjs.jcom_profile.models import JCOMProfile

PROFESSION_KEYS = [choice[0] for choice in PROFESSIONS]
LINK_FIELDS = (
    None,
    "records_scix",
    "records_inspire",
    "records_arxiv",
    "records_other",
    "facebook",
    "twitter",
    "linkedin",
)


def _reload(user: Account) -> Account:
    """Return a fresh Account instance with no cached related objects."""
    return Account.objects.get(pk=user.pk)


def _journal(code: str) -> SimpleNamespace:
    return SimpleNamespace(code=code)


def _make_jcom_valid(user: Account) -> Account:
    """Set every JCOM requirement to a valid value."""
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
    """Set every JCAP requirement to a valid value."""
    JCOMProfile.objects.filter(pk=user.pk).update(
        records_scix="https://scixplorer.org/author/doe",
        records_inspire="",
        records_arxiv="",
        records_other="",
    )
    return _reload(user)


def _clear_links(user: Account) -> None:
    JCOMProfile.objects.filter(pk=user.pk).update(
        records_scix="", records_inspire="", records_arxiv="", records_other=""
    )
    Account.objects.filter(pk=user.pk).update(facebook="", twitter="", linkedin="")


# -- frozen references: the behavior this refactoring must preserve ----------------------------


def _old_jcom_validation(user) -> bool:
    """`jcom_correspondence_author_validation` as in f84bae5c (duplicated `records_other` kept)."""
    jcomprofile = user.jcomprofile
    is_active = user.is_active
    valid_professions = {choice[0] for choice in PROFESSIONS}
    personal_data = bool(
        (user.last_name or user.first_name)
        and user.email
        and jcomprofile.profession in valid_professions
        and user.biography,
    )
    professional_data = bool(
        jcomprofile.records_scix
        or jcomprofile.records_inspire
        or jcomprofile.records_arxiv
        or jcomprofile.records_other
        or user.facebook
        or user.twitter
        or user.linkedin
        or jcomprofile.records_other,
    )
    return is_active and personal_data and professional_data


def _old_jcap_validation(user) -> bool:
    """`jcap_correspondence_author_validation` as in f84bae5c."""
    is_active = user.is_active
    personal_data = user.first_name and user.email
    jcomprofile = user.jcomprofile
    professional_data = user.affiliation() and bool(
        jcomprofile.records_scix
        or jcomprofile.records_inspire
        or jcomprofile.records_arxiv
        or jcomprofile.records_other
        or user.facebook
        or user.twitter
        or user.linkedin
        or jcomprofile.records_other,
    )
    return is_active and personal_data and professional_data


def _has_professional_links(user) -> bool:
    jcomprofile = user.jcomprofile
    return bool(
        jcomprofile.records_scix
        or jcomprofile.records_inspire
        or jcomprofile.records_arxiv
        or jcomprofile.records_other
        or user.facebook
        or user.twitter
        or user.linkedin,
    )


def _old_jcom_missing_fields(user) -> list[str]:
    """`jcom_correspondence_author_missing_fields` as of the branch HEAD before the required-fields dict."""
    missing = []
    if not user.is_active:
        missing.append("Active account")
    if not (user.last_name or user.first_name):
        missing.append("Name")
    if not user.email:
        missing.append("Email")
    if user.jcomprofile.profession not in {choice[0] for choice in PROFESSIONS}:
        missing.append("Profession")
    if not user.biography:
        missing.append("Biography")
    if not _has_professional_links(user):
        missing.append(str(PROFESSIONAL_LINKS_LABEL))
    return missing


def _old_jcap_missing_fields(user) -> list[str]:
    """`jcap_correspondence_author_missing_fields` as of the branch HEAD before the required-fields dict."""
    missing = []
    if not user.is_active:
        missing.append("Active account")
    if not user.first_name:
        missing.append("First name")
    if not user.email:
        missing.append("Email")
    if not user.affiliation():
        missing.append("Affiliation")
    if not _has_professional_links(user):
        missing.append(str(PROFESSIONAL_LINKS_LABEL))
    return missing


def _stub_user(
    *, is_active, first_name, last_name, email, biography, profession, affiliation, link
) -> SimpleNamespace:
    records = {"records_scix": "", "records_inspire": "", "records_arxiv": "", "records_other": ""}
    social = {"facebook": "", "twitter": "", "linkedin": ""}
    if link in records:
        records[link] = "x"
    elif link in social:
        social[link] = "x"
    return SimpleNamespace(
        is_active=is_active,
        first_name=first_name,
        last_name=last_name,
        email=email,
        biography=biography,
        jcomprofile=SimpleNamespace(profession=profession, **records),
        affiliation=lambda: affiliation,
        **social,
    )


def _assert_equivalent(old_validation, old_missing, validation, journal, user, case):
    missing = missing_required_fields(user, journal)
    assert isinstance(missing, list), case
    assert bool(old_validation(user)) == validation(user, journal), case
    assert [str(label) for label in missing] == old_missing(user), case
    assert validation(user, journal) == (not missing), case


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["JCOM", "JCOMAL"])
def test_jcom_validation_equivalent_to_old_validation(code):
    journal = _journal(code)
    professions = [None, -1, *PROFESSION_KEYS]
    for is_active, first_name, last_name, email, biography, profession, link in itertools.product(
        (True, False), ("", "A"), ("", "B"), ("", "e@x.org"), ("", "bio"), professions, LINK_FIELDS
    ):
        case = (code, is_active, first_name, last_name, email, biography, profession, link)
        user = _stub_user(
            is_active=is_active,
            first_name=first_name,
            last_name=last_name,
            email=email,
            biography=biography,
            profession=profession,
            affiliation="",
            link=link,
        )
        _assert_equivalent(
            _old_jcom_validation, _old_jcom_missing_fields, jcom_correspondence_author_validation, journal, user, case
        )


@pytest.mark.django_db
def test_jcap_validation_equivalent_to_old_validation():
    journal = _journal("JCAP")
    for is_active, first_name, last_name, email, affiliation, link in itertools.product(
        (True, False), ("", "A"), ("", "B"), ("", "e@x.org"), ("", "Uni"), LINK_FIELDS
    ):
        case = (is_active, first_name, last_name, email, affiliation, link)
        user = _stub_user(
            is_active=is_active,
            first_name=first_name,
            last_name=last_name,
            email=email,
            biography="",
            profession=None,
            affiliation=affiliation,
            link=link,
        )
        _assert_equivalent(
            _old_jcap_validation, _old_jcap_missing_fields, jcap_correspondence_author_validation, journal, user, case
        )


# -- jcom ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_jcom_validation_valid_user_passes(user: Account):
    valid_user = _make_jcom_valid(user)
    assert jcom_correspondence_author_validation(valid_user, _journal("JCOM")), "a complete jcom profile validates"
    assert missing_required_fields(valid_user, _journal("JCOMAL")) == []


@pytest.mark.django_db
def test_jcom_validation_inactive_user_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(is_active=False)
    assert not jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
def test_jcom_validation_missing_biography_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(biography="")
    assert not jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
@pytest.mark.parametrize("profession", [None, -1])
def test_jcom_validation_missing_or_out_of_choices_profession_fails(user: Account, profession):
    valid_user = _make_jcom_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=profession)
    reloaded = _reload(valid_user)
    assert not jcom_correspondence_author_validation(reloaded, _journal("JCOM"))
    assert [str(f) for f in missing_required_fields(reloaded, _journal("JCOM"))] == ["Profession"]


@pytest.mark.django_db
def test_jcom_validation_zero_profession_is_valid(user: Account):
    valid_user = _make_jcom_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=0)
    assert jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
def test_jcom_validation_missing_names_fails_but_either_name_suffices(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(first_name="", last_name="")
    assert not jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))
    Account.objects.filter(pk=user.pk).update(last_name="Doe")
    assert jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
def test_jcom_validation_missing_email_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(email="")
    assert not jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
def test_jcom_validation_no_professional_links_fails(user: Account):
    valid_user = _make_jcom_valid(user)
    _clear_links(user)
    assert not jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
def test_jcom_validation_social_handle_alone_passes(user: Account):
    """A single social handle is enough professional data, no records URLs needed."""
    valid_user = _make_jcom_valid(user)
    _clear_links(user)
    Account.objects.filter(pk=user.pk).update(facebook="https://facebook.com/doe")
    assert jcom_correspondence_author_validation(_reload(valid_user), _journal("JCOM"))


@pytest.mark.django_db
def test_jcom_missing_fields_lists_exactly_what_is_missing(user: Account):
    valid_user = _make_jcom_valid(user)
    Account.objects.filter(pk=user.pk).update(biography="", email="")
    JCOMProfile.objects.filter(pk=user.pk).update(profession=None)
    _clear_links(user)
    missing = missing_required_fields(_reload(valid_user), _journal("JCOM"))
    assert [str(field) for field in missing] == ["Email", "Profession", "Biography", str(PROFESSIONAL_LINKS_LABEL)]


# -- jcap ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_jcap_validation_valid_user_passes(user: Account):
    valid_user = _make_jcap_valid(user)
    assert jcap_correspondence_author_validation(valid_user, _journal("JCAP")), "a complete jcap profile validates"


@pytest.mark.django_db
def test_jcap_validation_inactive_user_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(is_active=False)
    assert not jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_validation_missing_first_name_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(first_name="")
    assert not jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_validation_missing_email_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(email="")
    assert not jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_validation_missing_affiliation_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    user.affiliations.all().delete()
    assert not jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_validation_no_professional_links_fails(user: Account):
    valid_user = _make_jcap_valid(user)
    _clear_links(user)
    assert not jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_validation_missing_last_name_still_passes(user: Account):
    """Unlike jcom, jcap only requires first_name, not last_name-or-first_name."""
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(last_name="")
    assert jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_validation_missing_profession_and_biography_still_passes(user: Account):
    """Unlike jcom, jcap does not require a profession nor a biography."""
    valid_user = _make_jcap_valid(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=None)
    Account.objects.filter(pk=user.pk).update(biography="")
    assert jcap_correspondence_author_validation(_reload(valid_user), _journal("JCAP"))


@pytest.mark.django_db
def test_jcap_missing_fields_lists_exactly_what_is_missing(user: Account):
    valid_user = _make_jcap_valid(user)
    Account.objects.filter(pk=user.pk).update(first_name="")
    user.affiliations.all().delete()
    _clear_links(user)
    missing = missing_required_fields(_reload(valid_user), _journal("JCAP"))
    assert [str(field) for field in missing] == ["First name", "Affiliation", str(PROFESSIONAL_LINKS_LABEL)]


# -- settings -----------------------------------------------------------------------------------


def test_validation_function_setting_resolves():
    """Every dotted path must be importable: a typo would only surface when a submission is validated."""
    override = settings.SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION
    assert None in override, "the override must define a None fallback (it fully replaces the default dict)"
    for journal_code, dotted_path in override.items():
        assert callable(import_string(dotted_path)), f"{dotted_path} (journal {journal_code!r}) must be callable"
    assert import_string(override["JCAP"]) is jcap_correspondence_author_validation
    for journal_code in ("JCOM", "JCOMAL"):
        assert import_string(override[journal_code]) is jcom_correspondence_author_validation


def test_required_fields_setting_shape():
    override = settings.SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS
    assert set(override) == {"JCOM", "JCOMAL", "JCAP", None}
    assert set(override) == set(settings.SUBMISSION_CORRESPONDENCE_AUTHOR_VALIDATION_FUNCTION)
    for entries in override.values():
        for entry in entries:
            assert entry["label"] and entry["paths"], entry
    assert override["JCOM"] == override["JCOMAL"]
    assert not hasattr(settings, "SUBMISSION_CORRESPONDENCE_AUTHOR_MISSING_FIELDS_FUNCTION")


def test_shipped_required_fields_entries_match_the_typed_dicts():
    """A typo in a key (e.g. `alowed`) would be silently ignored at runtime: pin the shipped dict to its types."""
    from wjs.jcom_profile.constants import ProfileHint, ProfileRequiredFieldEntry

    assert ProfileRequiredFieldEntry.__required_keys__ == {"label", "paths"}
    assert ProfileRequiredFieldEntry.__optional_keys__ == {"allowed", "profile"}
    assert ProfileHint.__required_keys__ == {"section", "fields"}
    assert ProfileHint.__optional_keys__ == {"group_help"}
    known = ProfileRequiredFieldEntry.__required_keys__ | ProfileRequiredFieldEntry.__optional_keys__
    known_hint = ProfileHint.__required_keys__ | ProfileHint.__optional_keys__
    rules = settings.SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS
    assert None in rules
    for journal_code, entries in rules.items():
        for entry in entries:
            assert ProfileRequiredFieldEntry.__required_keys__ <= entry.keys(), (journal_code, entry)
            assert entry.keys() <= known, (journal_code, entry)
            if "profile" in entry:
                assert ProfileHint.__required_keys__ <= entry["profile"].keys() <= known_hint, (journal_code, entry)
