"""
Required profile fields, as seen from the profile pages.

When an author reaches the profile from the submission (the "Back to submission" link), the profile pages tell
which fields the journal requires of a correspondence author and which are still missing. The rules are the
journal's entries of `SUBMISSION_CORRESPONDENCE_AUTHOR_REQUIRED_FIELDS` (the same dict that drives validation
and submission step 4); an entry's optional ``"profile"`` key says where it can be fixed.
Nothing is stored: everything is computed from the request and the user's current data.
"""

from dataclasses import dataclass

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme
from plugins.wjs_submission.account_validation import (
    get_required_field_entries,
    is_required_field_entry_satisfied,
    is_whitelisted_correspondence_author,
)


@dataclass(frozen=True)
class RequiredEntry:
    """
    A required-fields entry that can be fixed in the profile, and whether it is still missing.

    `section` and `fields` come from the entry's `profile` hint (see `ProfileHint`), as does the optional
    `group_help`: the help text of the entry's optional fields when it spans several of them; without it the
    help text names the fields.
    """

    label: str
    section: str
    fields: tuple[str, ...]
    missing: bool
    group_help: str | None = None


def safe_back_to_submission_url(request: HttpRequest) -> str | None:
    """
    Return the URL of the submission the user came from, if it is stored in the session and safe to follow.

    :param request: The current request
    :return: The stored "next" URL, or None if it is missing or points to another site.
    """
    next_url = request.session.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None


def get_correspondence_author_requirements(request: HttpRequest) -> list[RequiredEntry]:
    """
    Return the journal's required entries that can be fixed in the profile, for an author coming from submission.

    The list is empty unless the user is authenticated, came from the submission, a journal is set and the user is
    not whitelisted (whitelisted users are eligible whatever their profile contains).

    :param request: The current request
    :return: One item per entry with a ``"profile"`` hint, in the order of the journal's rules.
    """
    journal = getattr(request, "journal", None)
    user = request.user
    if (
        journal is None
        or not user.is_authenticated
        or safe_back_to_submission_url(request) is None
        or is_whitelisted_correspondence_author(journal, user)
    ):
        return []
    return [
        RequiredEntry(
            label=entry["label"],
            section=entry["profile"]["section"],
            fields=tuple(entry["profile"]["fields"]),
            missing=not is_required_field_entry_satisfied(user, entry),
            group_help=entry["profile"].get("group_help"),
        )
        for entry in get_required_field_entries(journal)
        if "profile" in entry
    ]
