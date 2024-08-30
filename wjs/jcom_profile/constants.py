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
REVIEWER_ROLE = "reviewer"
EO_GROUP = "EO"
TYPESETTER_ROLE = "typesetter"

LABELS = {
    DIRECTOR_ROLE: _("Director"),
    DIRECTOR_MAIN_ROLE: _("Director"),
    EDITOR_ROLE: _("Editor"),
    SECTION_EDITOR_ROLE: _("Editor"),
    AUTHOR_ROLE: _("Author"),
    REVIEWER_ROLE: _("Reviewer"),
    EO_GROUP: _("EO"),
    TYPESETTER_ROLE: _("Typesetter"),
}
