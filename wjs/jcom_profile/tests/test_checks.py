"""
Tests for the consistency check between the required-fields rules and the fields each journal shows.

A rule naming a field the journal hides would make validation depend on something the author cannot see or edit
(wjs/specs#3392).
"""

from django.conf import settings
from django.core import checks

from wjs.jcom_profile.checks import (
    check_correspondence_author_required_fields,
    find_required_fields_inconsistencies,
)


def _entry(label, paths, fields):
    return {"label": label, "paths": paths, "profile": {"section": "s", "fields": fields}}


def test_shipped_settings_are_consistent():
    assert (
        find_required_fields_inconsistencies(
            settings.SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS, settings.PROFILE_FIELDS
        )
        == []
    )


def test_hidden_field_in_a_rule_is_reported():
    rules = {None: [], "JCAP": [_entry("Links", ["facebook"], ["facebook"])]}
    profile_fields = {None: ("facebook",), "JCAP": ("records_scix",)}
    messages = find_required_fields_inconsistencies(rules, profile_fields)
    assert len(messages) == 1
    assert "JCAP" in messages[0]
    assert "Links" in messages[0]
    assert "facebook" in messages[0]


def test_paths_and_profile_fields_must_match():
    rules = {None: [_entry("Links", ["jcomprofile.records_scix", "facebook"], ["records_scix"])]}
    profile_fields = {None: ("records_scix", "facebook")}
    messages = find_required_fields_inconsistencies(rules, profile_fields)
    assert len(messages) == 1
    assert "Links" in messages[0]


def test_entries_without_a_profile_hint_or_fields_are_skipped():
    """Active account and affiliation are not profile fields of `PROFILE_FIELDS`: they must not be reported."""
    rules = {
        None: [
            {"label": "Active", "paths": ["is_active"]},
            {"label": "Affiliation", "paths": ["affiliation"], "profile": {"section": "a", "fields": []}},
        ]
    }
    assert find_required_fields_inconsistencies(rules, {None: ("email",)}) == []


def test_journals_are_resolved_through_the_fallbacks():
    bad = _entry("Links", ["facebook"], ["facebook"])
    # a journal only in `profile_fields` (JQuant) uses the fallback rules
    messages = find_required_fields_inconsistencies({None: [bad]}, {None: ("email",), "JQuant": ("email",)})
    assert sorted(message.split(":", 1)[0] for message in messages) == ["JQuant", "default"]
    # a journal only in the rules is checked against the fallback fields
    messages = find_required_fields_inconsistencies({None: [], "JCOM": [bad]}, {None: ("email",)})
    assert [message.split(":", 1)[0] for message in messages] == ["JCOM"]


def test_missing_none_fallback_is_reported_not_raised():
    """An override without the `None` key is what the check is for: report it, do not crash with a KeyError."""
    entry = _entry("Links", ["facebook"], ["facebook"])
    messages = find_required_fields_inconsistencies({"JCAP": [entry]}, {None: ("facebook",), "JCAP": ("facebook",)})
    assert len(messages) == 1
    assert messages[0].startswith("default:")
    assert "required fields" in messages[0]
    messages = find_required_fields_inconsistencies({None: [entry]}, {"JCAP": ("facebook",)})
    assert len(messages) == 1
    assert messages[0].startswith("default:")
    assert "profile fields" in messages[0]
    messages = find_required_fields_inconsistencies({"JCAP": [entry]}, {"JCAP": ("facebook",)})
    assert len(messages) == 1
    assert "required fields and profile fields" in messages[0]


def test_registered_check_returns_errors(settings):
    assert check_correspondence_author_required_fields(None) == []
    settings.PROFILE_FIELDS = {
        **settings.PROFILE_FIELDS,
        "JCAP": tuple(field for field in settings.PROFILE_FIELDS["JCAP"] if field != "records_arxiv"),
    }
    errors = check_correspondence_author_required_fields(None)
    assert errors
    assert all(isinstance(error, checks.Error) and error.id == "wjs_profile.E001" for error in errors)
    assert any("records_arxiv" in error.msg for error in errors)
