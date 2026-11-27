"""
Tests for the highlight of the profile fields required for submission.

Everything is shown only when the author reaches the profile from the submission (session "next").
"""

import re

import pytest
from core.models import Account
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from django.urls import reverse
from journal.models import Journal
from plugins.wjs_submission.models import WhitelistedCorrespondenceAuthors

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.profile.forms import (
    WjsAdditionalInfoForm,
    WjsEmailChangeForm,
    WjsPersonalInfoForm,
)
from wjs.jcom_profile.profile.required_fields import (
    RequiredEntry,
    get_correspondence_author_requirements,
    safe_back_to_submission_url,
)

STEP_URL = "/JCOM/plugins/wjs-submission/submission/1/4/"
PROFILE_SECTIONS = {
    "core_edit_profile",
    "core_edit_profile_affiliations",
    "core_edit_profile_email",
    "core_edit_profile_additional",
}


def _reload(user: Account) -> Account:
    return Account.objects.get(pk=user.pk)


def _request(user, journal, next_url=STEP_URL):
    request = RequestFactory().get("/JCOM/profile/")
    request.user = user
    request.journal = journal
    request.session = {"next": next_url} if next_url else {}
    return request


def _incomplete_jcom_user(user: Account) -> Account:
    """A JCOM user who lacks biography and any professional link, but has everything else."""
    Account.objects.filter(pk=user.pk).update(biography="", facebook="", twitter="", linkedin="")
    JCOMProfile.objects.filter(pk=user.pk).update(
        profession=0, records_scix="", records_inspire="", records_arxiv="", records_other=""
    )
    return _reload(user)


@pytest.mark.django_db
def test_requirements_empty_without_next(user: Account, journal: Journal):
    assert get_correspondence_author_requirements(_request(_incomplete_jcom_user(user), journal, next_url=None)) == []


@pytest.mark.django_db
def test_requirements_empty_with_external_next(user: Account, journal: Journal):
    request = _request(_incomplete_jcom_user(user), journal, next_url="https://evil.example.com/")
    assert safe_back_to_submission_url(request) is None
    assert get_correspondence_author_requirements(request) == []


@pytest.mark.django_db
def test_requirements_empty_for_anonymous(journal: Journal):
    assert get_correspondence_author_requirements(_request(AnonymousUser(), journal)) == []


@pytest.mark.django_db
def test_requirements_empty_for_whitelisted_user(user: Account, journal: Journal):
    incomplete = _incomplete_jcom_user(user)
    WhitelistedCorrespondenceAuthors.objects.create(user=user, journal=journal)
    assert get_correspondence_author_requirements(_request(incomplete, journal)) == []


@pytest.mark.django_db
def test_requirements_jcom_lists_hinted_entries_in_order_flagging_the_missing_ones(user: Account, journal: Journal):
    entries = get_correspondence_author_requirements(_request(_incomplete_jcom_user(user), journal))
    assert [str(entry.label) for entry in entries] == [
        "Name",
        "Email",
        "Profession",
        "Biography",
        LINKS_LABEL,
    ], "Active account has no profile hint and must not be listed"
    assert [entry.missing for entry in entries] == [False, False, False, True, True]
    by_label = {str(entry.label): entry for entry in entries}
    assert all(isinstance(entry, RequiredEntry) for entry in entries)
    assert by_label["Biography"].section == "core_edit_profile"
    assert by_label["Biography"].fields == ("biography",)
    links = entries[-1]
    assert links.section == "core_edit_profile_additional"
    assert set(links.fields) == {"records_other", "facebook", "twitter", "linkedin"}
    assert by_label["Name"].fields == ("first_name", "last_name"), "in the order of the form"
    assert by_label["Email"].section == "core_edit_profile_email"


@pytest.mark.django_db
def test_requirements_jcap_links_entry_names_only_records_fields(user: Account, journal_factory):
    jcap = journal_factory("JCAP")
    entries = get_correspondence_author_requirements(_request(_reload(user), jcap))
    links = next(entry for entry in entries if entry.section == "core_edit_profile_additional")
    assert set(links.fields) == {"records_scix", "records_inspire", "records_arxiv", "records_other"}


@pytest.mark.django_db
def test_requirements_jcap_affiliation_entry_points_to_the_affiliations_tab(user: Account, journal_factory):
    jcap = journal_factory("JCAP")
    user.affiliations.all().delete()
    entries = get_correspondence_author_requirements(_request(_reload(user), jcap))
    affiliation = next(entry for entry in entries if str(entry.label) == "Affiliation")
    assert affiliation.missing
    assert affiliation.section == "core_edit_profile_affiliations"
    assert affiliation.fields == ()


@pytest.mark.django_db
def test_requirements_only_inactive_account_missing_flags_nothing(user: Account, journal: Journal):
    complete = _incomplete_jcom_user(user)
    Account.objects.filter(pk=user.pk).update(biography="bio", facebook="https://facebook.com/x", is_active=False)
    entries = get_correspondence_author_requirements(_request(_reload(complete), journal))
    assert entries
    assert not any(entry.missing for entry in entries)


@pytest.mark.django_db
def test_requirements_unknown_journal_code_uses_the_none_rules(user: Account, journal_factory):
    other = journal_factory("OTHER")
    entries = get_correspondence_author_requirements(_request(_reload(user), other))
    assert [str(entry.label) for entry in entries] == ["Name", "Email", "Affiliation"]


def test_required_fields_setting_hints_use_known_sections():
    for journal_code, entries in settings.SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS.items():
        for entry in entries:
            hint = entry.get("profile")
            if hint is None:
                assert str(entry["label"]) == "Active account", (journal_code, entry["label"])
                continue
            assert hint["section"] in PROFILE_SECTIONS, (journal_code, entry["label"])
            assert isinstance(hint["fields"], list)


# -- Required help text on the forms -----------------------------------------------------------

GROUP_TEXT = "fill in at least one of: "
LINKS_GROUP_HELP = "fill in at least one of the fields in this page"
LINK_FIELDS = ("records_scix", "records_inspire", "records_arxiv", "records_other", "facebook", "twitter", "linkedin")


def _entry(label, section, fields, missing=True, group_help=None):
    return RequiredEntry(label=label, section=section, fields=tuple(fields), missing=missing, group_help=group_help)


def _profile(user: Account) -> JCOMProfile:
    return JCOMProfile.objects.get(pk=user.pk)


@pytest.mark.django_db
def test_personal_form_without_requirements_is_unchanged(user: Account, journal: Journal):
    form = WjsPersonalInfoForm(instance=_profile(user), journal=journal)
    assert str(form.fields["last_name"].help_text) == "Required", "today's plain Required on required fields"
    assert "<strong>" not in str(form.fields["biography"].help_text)


@pytest.mark.django_db
def test_personal_form_required_help_text_keeps_the_existing_help(user: Account, journal: Journal):
    requirements = [
        _entry("Biography", "core_edit_profile", ["biography"]),
        _entry("Profession", "core_edit_profile", ["profession"], missing=False),
    ]
    form = WjsPersonalInfoForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    biography = str(form.fields["biography"].help_text)
    assert biography.startswith(
        '<strong class="text-primary">Required</strong> - '
    ), "the theme colours only help text following an input or select: the editor field gets the colour explicitly"
    assert "Biographies are compulsory for authors" in biography
    assert str(form.fields["profession"].help_text) == "<strong>Required</strong>", "also when already filled"


@pytest.mark.django_db
def test_personal_form_name_group_says_at_least_one_only_on_the_optional_field(user: Account, journal: Journal):
    requirements = [_entry("Name", "core_edit_profile", ["first_name", "last_name"])]
    form = WjsPersonalInfoForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    assert form.fields["last_name"].required, "last_name is always required by the form"
    assert str(form.fields["last_name"].help_text) == "<strong>Required</strong>", "no misleading 'at least one'"
    first_name = str(form.fields["first_name"].help_text)
    assert first_name.startswith("<strong>Required</strong>")
    # What the template renders are the bound fields, which copy the help text when they are first built:
    assert str(form["first_name"].help_text) == first_name, "the rendered first name carries the new help text"
    assert str(form["last_name"].help_text) == "<strong>Required</strong>"
    assert f"{GROUP_TEXT}First name, Last name" in first_name, "the text names the fields of the group"
    assert "these fields" not in first_name


@pytest.mark.django_db
def test_additional_form_uses_the_entry_group_help_without_naming_the_fields(user: Account, journal: Journal):
    """The links entry gives its own text: journals show different links, so no field is named."""
    requirements = [_entry(LINKS_LABEL, "core_edit_profile_additional", LINK_FIELDS, group_help=LINKS_GROUP_HELP)]
    form = WjsAdditionalInfoForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    for name in ("facebook", "twitter", "linkedin", "records_other"):
        help_text = str(form[name].help_text)
        assert help_text == f"<strong>Required</strong> - {LINKS_GROUP_HELP}", name


def test_shipped_links_entry_has_a_short_label_and_a_group_help():
    for journal_code in ("JCOM", "JCOMAL", "JCAP"):
        entry = next(
            entry
            for entry in settings.SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS[journal_code]
            if entry.get("profile", {}).get("section") == "core_edit_profile_additional"
        )
        assert str(entry["label"]) == LINKS_LABEL
        assert str(entry["profile"]["group_help"]) == LINKS_GROUP_HELP


@pytest.mark.django_db
def test_forms_ignore_entries_of_other_sections(user: Account, journal: Journal):
    requirements = [_entry("Links", "core_edit_profile_additional", LINK_FIELDS)]
    form = WjsPersonalInfoForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    assert "<strong>" not in str(form.fields["biography"].help_text)
    email_form = WjsEmailChangeForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    assert "<strong>" not in str(email_form.fields["email"].help_text)


@pytest.mark.django_db
def test_additional_form_links_group_marks_every_link_field(user: Account, journal: Journal):
    requirements = [_entry("Links", "core_edit_profile_additional", LINK_FIELDS)]
    form = WjsAdditionalInfoForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    for field in LINK_FIELDS:
        help_text = str(form.fields[field].help_text)
        assert help_text.startswith("<strong>Required</strong> - "), field
        assert GROUP_TEXT in help_text, field
        assert "these fields" not in help_text, field
        for label in (str(form[name].label) for name in LINK_FIELDS):
            assert label in help_text, (field, label)


@pytest.mark.django_db
def test_email_form_marks_the_email_field(user: Account, journal: Journal):
    requirements = [_entry("Email", "core_edit_profile_email", ["email"], missing=False)]
    form = WjsEmailChangeForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    assert str(form.fields["email"].help_text) == "<strong>Required</strong>"


def _profile_client(user: Account, from_submission: bool) -> Client:
    jcom_user = JCOMProfile.objects.get(janeway_account=user)
    jcom_user.gdpr_checkbox = True
    jcom_user.is_active = True
    jcom_user.save()
    client = Client()
    client.force_login(jcom_user)
    if from_submission:
        session = client.session
        session["next"] = STEP_URL
        session.save()
    return client


@pytest.mark.django_db
def test_additional_page_help_text_is_generic_and_names_no_link(user: Account, journal: Journal):
    _incomplete_jcom_user(user)
    page = _profile_client(user, from_submission=True).get(f"/{journal.code}/profile/info/").content.decode()
    assert re.search(
        rf'id="id_facebook_helptext"[^>]*><strong>Required</strong> - {LINKS_GROUP_HELP}\s*<', page
    ), "the facebook field carries the generic group help text"
    for hidden in ("SciX", "INSPIRE", "arXiv"):
        assert hidden not in re.sub(r"\s+", " ", page.split("wjs-profile-form__title", 1)[1].split("<form", 1)[0])


@pytest.mark.django_db
def test_profile_page_shows_required_help_text_only_when_coming_from_submission(user: Account, journal: Journal):
    _incomplete_jcom_user(user)
    from_submission = _profile_client(user, from_submission=True).get(f"/{journal.code}/profile/")
    page = from_submission.content.decode()
    assert "<strong>Required</strong>" in page
    assert re.search(
        r'id="id_first_name_helptext"[^>]*><strong>Required</strong> - fill in at least one of: First name, Last name',
        page,
    ), "the rendered page (not just the form fields) carries the name group help text"
    elsewhere = _profile_client(user, from_submission=False).get(f"/{journal.code}/profile/")
    assert "<strong>Required</strong>" not in elsewhere.content.decode()


# -- Global banner, tab banners and tab badges ------------------------------------------------

LINKS_LABEL = "Additional information (at least one field)"


def _badge(html: str, tab: str) -> str | None:
    """Return the badge number shown on a profile tab link, if any."""
    link = re.search(rf"<a[^>]*wjs-profile-nav__link[^>]*>\s*{tab}\s*(.*?)</a>", html, re.DOTALL)
    assert link, f"tab {tab} not found"
    badge = re.search(r'class="badge[^"]*"[^>]*>\s*(\d+)\s*<', link.group(1))
    return badge.group(1) if badge else None


def _global_banner(html: str) -> str | None:
    """Return the text of the global banner (it sits right above the sidebar/content row), if shown."""
    start = html.find('id="wjs-profile-correspondence-author-requirements"')
    if start == -1:
        return None
    start = html.index(">", start) + 1
    end = html.index('<div class="row">', start)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[start:end])).strip()


def _tab_banner(html: str) -> str | None:
    """Return the text of the banner under the tab title, if shown (it must sit between the title and the form)."""
    title_end = html.index("wjs-profile-form__title")
    match = re.search(r'id="wjs-profile-tab-requirements"[^>]*>(.*?)</div>', html[title_end:], re.DOTALL)
    if match is None:
        assert 'id="wjs-profile-tab-requirements"' not in html
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()


@pytest.mark.django_db
def test_global_banner_points_to_the_highlighted_tabs_without_listing_fields(user: Account, journal: Journal):
    _incomplete_jcom_user(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=None)
    html = _profile_client(user, from_submission=True).get(f"/{journal.code}/profile/").content.decode()
    banner = _global_banner(html)
    assert banner
    assert "JCOM" in banner
    assert banner == (
        "Your profile is incomplete for submission to JCOM, as required by the journal ethical policy, "
        "please fill in the tabs highlighted in the sidebar. "
        'Click "Back to submission" after entering and saving the required information.'
    )
    for label in ("Profession", "Biography", LINKS_LABEL):
        assert label not in banner, "the fields are listed in the tabs, not in the global banner"
    assert _badge(html, "Personal Details") == "2"
    assert _badge(html, "Additional information") == "1"
    assert _badge(html, "Affiliations") is None


@pytest.mark.django_db
def test_each_tab_lists_its_own_missing_fields_under_the_title(user: Account, journal: Journal):
    _incomplete_jcom_user(user)
    JCOMProfile.objects.filter(pk=user.pk).update(profession=None)
    client = _profile_client(user, from_submission=True)
    personal = _tab_banner(client.get(f"/{journal.code}/profile/").content.decode())
    assert personal == "Still to fill in on this page: Profession, Biography"
    additional = _tab_banner(client.get(f"/{journal.code}/profile/info/").content.decode())
    assert additional == f"Still to fill in on this page: {LINKS_LABEL}"
    assert _tab_banner(client.get(f"/{journal.code}/profile/email/").content.decode()) is None, "nothing missing there"


@pytest.mark.django_db
def test_no_banner_nor_badge_without_coming_from_submission(user: Account, journal: Journal):
    _incomplete_jcom_user(user)
    client = _profile_client(user, from_submission=False)
    for url in (
        f"/{journal.code}/profile/",
        f"/{journal.code}/profile/info/",
        f"/{journal.code}/profile/email/",
        reverse("edit_newsletters"),
    ):
        html = client.get(url).content.decode()
        assert "alert-warning" not in html, url
        assert "wjs-profile-tab-requirements" not in html, url
        assert "text-bg-warning" not in html, url
        assert "<strong>Required</strong>" not in html, url


@pytest.mark.django_db
def test_banners_and_badges_disappear_after_saving_the_missing_fields(user: Account, journal: Journal):
    _incomplete_jcom_user(user)
    Account.objects.filter(pk=user.pk).update(facebook="https://facebook.com/doe")
    client = _profile_client(user, from_submission=True)
    page = f"/{journal.code}/profile/"
    before = client.get(page).content.decode()
    assert _tab_banner(before) == "Still to fill in on this page: Biography"
    assert _badge(before, "Personal Details") == "1"

    response = client.post(
        page,
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "profession": "0",
            "career_stage": "0",
            "biography": "<p>A short bio.</p>",
            "gdpr_checkbox": "on",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]

    after = client.get(page).content.decode()
    assert _global_banner(after) is None
    assert _tab_banner(after) is None
    assert _badge(after, "Personal Details") is None
    assert "<strong>Required</strong>" in after, "Required stays on the required fields, filled or not"


@pytest.mark.django_db
def test_affiliations_tab_banner_badge_and_required_note(user: Account, journal: Journal):
    Journal.objects.filter(pk=journal.pk).update(code="JCAP")
    user.affiliations.all().delete()
    client = _profile_client(user, from_submission=True)
    html = client.get("/JCAP/profile/affiliations/").content.decode()
    assert _badge(html, "Affiliations") == "1"
    assert _tab_banner(html) == "Still to fill in on this page: Affiliation"
    body = html.split("wjs-profile-form__title")[1]
    assert "<strong>Required</strong>" in body.split("<form")[0], "the note sits above the affiliations list"


@pytest.mark.django_db
def test_required_word_is_coloured_explicitly_only_where_the_theme_does_not(user: Account, journal: Journal):
    requirements = [
        _entry("Profession", "core_edit_profile", ["profession"]),
        _entry("Biography", "core_edit_profile", ["biography"]),
        _entry("Name", "core_edit_profile", ["first_name", "last_name"]),
    ]
    form = WjsPersonalInfoForm(
        instance=_profile(user), journal=journal, correspondence_author_requirements=requirements
    )
    assert str(form.fields["profession"].help_text) == "<strong>Required</strong>", "select: the theme colours it"
    assert str(form.fields["last_name"].help_text) == "<strong>Required</strong>", "input: the theme colours it"
    assert str(form.fields["biography"].help_text).startswith('<strong class="text-primary">Required</strong>')
