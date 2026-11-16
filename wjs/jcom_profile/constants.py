"""Roles slugs and other constants.

When working on roles, please check
- src/utils/install/roles.json
- plugins (e.g. typesetting plugin)

This module is imported by `wjs/defaults/settings.py` at settings load time: do not import models or apps here.
"""

from typing import NotRequired, TypedDict

from django.db.models import BLANK_CHOICE_DASH
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from plugins.wjs_submission.required_fields_types import RequiredFieldEntry

DIRECTOR_ROLE = "director"
DIRECTOR_MAIN_ROLE = "director-main"
EDITOR_ROLE = "editor"
SECTION_EDITOR_ROLE = "section-editor"
AUTHOR_ROLE = "author"
COAUTHOR_ROLE = "co-author"
REVIEWER_ROLE = "reviewer"
EO_GROUP = "EO"
TYPESETTER_ROLE = "typesetter"

LABELS = {
    DIRECTOR_ROLE: _("Director"),
    DIRECTOR_MAIN_ROLE: _("Director"),
    EDITOR_ROLE: _("Editor"),
    SECTION_EDITOR_ROLE: _("Editor"),
    AUTHOR_ROLE: _("Author"),
    COAUTHOR_ROLE: _("Co-author"),
    REVIEWER_ROLE: _("Reviewer"),
    EO_GROUP: _("EO"),
    TYPESETTER_ROLE: _("Typesetter"),
}


def role_label(role):
    return LABELS.get(role, role)


# In JCOM (and JCOMAL), the pubid depends on a code that depends on the section (article type).
# This is peculiar of JCOM* and does not apply to other journals.
JCOM_SECTION_TO_PUBIDSECTIONCODE = {
    "letter": "L",
    "article": "A",
    "commentary": "C",
    "essay": "Y",
    "editorial": "E",
    "conference review": "R",  # Same code for conference and book review
    "book review": "R",  # Same code for conference and book review
    "practice insight": "N",
    "focus": "F",
    "review article": "V",
    "erratum": "X",
    "addendum": "Z",
}

# In JCAP (until JCAP_JQUANT_STYLE_SINCE), the eid contains a section code only for these sections
# (all the other papers get a plain progressive number).
# JQuant-style eids (JQuant, JINST, JSTAT and later JCAP) never contain the section code.
SECTIONS_WITH_LETTERED_EID = ("Erratum", "Addendum")

# Obsolete!
# The DOI also used to depend on the section, until May 2025.
# We are leaving the old mapping here for documentation, but it should not be used.
JCOM_SECTION_TO_DOISECTIONCODE = {
    "letter": "01",
    "article": "02",
    "commentary": "03",
    "essay": "04",
    "editorial": "05",
    "conference review": "06",
    "book review": "07",
    "practice insight": "08",
    "focus": "09",  # Warning: focus and review article have the same code!!!
    "review article": "09",  # Probably not important: no focus for many years (as of 2023)!
}
# NB: reviews (conference review and book review) are a bit confusing:
# - they are counted together (as if they were in the same section; e.g. CR-1, BR-2, CR-3)
# - have same PUBID section code (both are "R", as in JCOM_0000_0000_R00)
# - have different DOI section code (e.g. prefix/0.00000600 vs prefix/0.00000700)

ORCID_VALIDATION_REGEXP = r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]{1}$"


GENDER_CHOICES = [
    ("M", "Male"),
    ("F", "Female"),
    ("D", "Rather not say"),
]

GENDER_FORM_CHOICES = BLANK_CHOICE_DASH + GENDER_CHOICES

PROFESSIONS = [
    (
        0,
        "A researcher in S&T studies," " science communication or neighbouring field",
    ),
    (
        1,
        "A practitioner in S&T" " (e.g. journalist, museum staff, writer, ...)",
    ),
    (2, "An active scientist"),
    (3, "Other profession"),
]

PROFESSIONS_FORM = BLANK_CHOICE_DASH + PROFESSIONS

PROFESSIONAL_LINKS_LABEL = _("Additional information (at least one field)")
"""
The "any of" requirement of correspondence authors is a single entry: the user fills in just one of the fields.

It names the profile tab instead of the fields: journals show different links (see `PROFILE_FIELDS`).
"""

PROFESSIONAL_LINKS_GROUP_HELP = _("fill in at least one of the fields in this page")
"""Help text of the fields of the professional links entry, naming no field (see `PROFESSIONAL_LINKS_LABEL`)."""

CAREER_STAGES = [
    (0, "Student (i.e. pre-PhD)"),
    (1, "Early career (e.g. postdoc, fixed-term contract)"),
    (2, "Tenure track"),
    (3, "Permanent (tenured or indefinite contract)"),
]

CAREER_STAGES_FORM = BLANK_CHOICE_DASH + CAREER_STAGES


class ProfileHint(TypedDict):
    """Where the profile pages let the author fix a required-fields entry."""

    section: str
    """Url name of the profile tab: `core_edit_profile`, `core_edit_profile_affiliations`, `core_edit_profile_email` or
    `core_edit_profile_additional`."""

    fields: list[str]
    """Names of the form fields of that tab holding the entry (empty for the Affiliations tab, a list rather than a
    form)."""

    group_help: NotRequired[str | Promise]
    """
    Help text of the entry's optional fields, for an entry spanning several fields ("at least one of").

    Without it the help text names the fields of the entry; use it when the fields to name vary per journal or are
    all the fields of the tab.
    """


class ProfileRequiredFieldEntry(RequiredFieldEntry):
    """A wjs-submission required-fields entry plus the profile location of the field."""

    profile: NotRequired[ProfileHint]
    """
    Where to fix the entry on the profile pages.

    wjs-submission ignores it; the profile pages use it to mark the fields as required and to place banners and
    badges. Entries without it (e.g. "Active account") still block validation and appear in step 4, but are never
    shown on the profile pages.
    """
