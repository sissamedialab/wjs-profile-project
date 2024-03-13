from core.models import Account
from django.urls import reverse
from journal.models import Journal

from wjs.jcom_profile import permissions


def user_primary_role_page(journal: Journal, user: Account):
    """Redirect the user to his "main" page.

    If a user has multiple roles (e.g. reviewer and author, or editor and reviewer and author),
    he can switch between the "role" pages from the dedicated menù in the navbar of logged-in users.
    """
    # FIXME: Refactor this after permissions checks have been renamed
    director = user.check_role(journal, "director")
    reviewer = user.check_role(journal, "reviewer")
    author = user.check_role(journal, "author")
    section_editor = user.check_role(journal, "section-editor")
    if permissions.is_eo(user) or user.is_staff:
        return reverse("wjs_review_eo_pending")
    elif director:
        # FIXME: List missing
        return reverse("wjs_review_reviewer_pending")
    elif section_editor:
        return reverse("wjs_review_list")
    elif reviewer:
        return reverse("wjs_review_reviewer_pending")
    elif author:
        return reverse("wjs_review_author_pending")
    else:
        return reverse("website_index")
