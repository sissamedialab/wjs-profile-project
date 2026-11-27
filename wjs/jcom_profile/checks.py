"""
Consistency check between the correspondence-author rules and the fields each journal shows.

`SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` says what a correspondence author must fill in, `PROFILE_FIELDS`
says which fields a journal shows in the profile. A rule naming a field the journal hides would make eligibility
depend on something the author cannot see or edit: the check fails at startup instead (`manage.py check`).
The opposite is fine: a journal may show fields that no rule requires.
"""

from collections.abc import Mapping, Sequence

from django.conf import settings
from django.core.checks import Error, register


def find_required_fields_inconsistencies(
    required_fields: Mapping[str | None, Sequence[Mapping]],
    profile_fields: Mapping[str | None, Sequence[str]],
) -> list[str]:
    """
    Return a message for each rule that disagrees with the fields its journal shows; empty if consistent.

    Every journal code of either dict is checked. Rules and visible fields are resolved with the fallback the runtime
    uses (`required_fields[None]`, as `get_required_field_entries` in wjs-submission, and `profile_fields[None]`, as
    `is_field_available`). Only entries that can be fixed in the profile (non-empty ``profile["fields"]``) are
    checked: each of those fields must be shown by the journal, and the fields read by validation (the last segment
    of each of the entry's ``paths``) must be the same as the fields highlighted in the profile.

    :param required_fields: The rules, per journal code (``None`` is the fallback)
    :param profile_fields: The fields shown, per journal code (``None`` is the fallback)
    :return: Sorted messages, each starting with the journal code (``default`` for the fallback)
    """
    messages = []
    no_fallback = [
        label
        for label, values in (("required fields", required_fields), ("profile fields", profile_fields))
        if None not in values
    ]
    if no_fallback:
        messages.append(f"default: no fallback (None) entry in {' and '.join(no_fallback)}")
    for code in set(required_fields) | set(profile_fields):
        name = code or "default"
        entries = required_fields.get(code, required_fields.get(None))
        visible = profile_fields.get(code, profile_fields.get(None))
        if entries is None or visible is None:
            continue  # the missing fallback is already reported above
        shown = set(visible)
        for entry in entries:
            fields = set(entry.get("profile", {}).get("fields", []))
            if not fields:
                continue
            hidden = sorted(fields - shown)
            if hidden:
                messages.append(
                    f"{name}: entry '{entry['label']}' names fields the journal does not show: {', '.join(hidden)}"
                )
            validated = {path.rsplit(".", 1)[-1] for path in entry["paths"]}
            if validated != fields:
                messages.append(
                    f"{name}: entry '{entry['label']}' validates {', '.join(sorted(validated))} "
                    f"but highlights {', '.join(sorted(fields))} in the profile"
                )
    return sorted(messages)


@register()
def check_correspondence_author_required_fields(app_configs, **kwargs) -> list[Error]:
    """Report the rules of the live settings that disagree with `PROFILE_FIELDS` (see the module docstring)."""
    from plugins.wjs_submission import settings as submission_settings

    messages = find_required_fields_inconsistencies(
        submission_settings.CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS, settings.PROFILE_FIELDS
    )
    return [Error(message, id="wjs_profile.E001") for message in messages]
