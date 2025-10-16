"""Roles slugs and other constants.

When working on roles, please check
- src/utils/install/roles.json
- plugins (e.g. typesetting plugin)
"""

from django.utils.translation import gettext_lazy as _

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
}

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
