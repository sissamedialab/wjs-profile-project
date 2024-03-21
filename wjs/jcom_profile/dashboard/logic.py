from core.models import Account
from django.urls import reverse
from journal.models import Journal

from wjs.jcom_profile import permissions


def user_primary_role_page(journal: Journal, user: Account):
    """
    Redirect the user to their "main" page.

    If a user has multiple roles (e.g. reviewer and author, or editor and reviewer and author),
    they can switch between the "role" pages from the dedicated menù in the navbar of logged-in users.
    """
    if permissions.has_admin_role(journal, user):
        return reverse("wjs_review_eo_pending")
    elif permissions.has_director_role(journal, user):
        return reverse("wjs_review_director_pending")
    elif permissions.has_any_editor_role(journal, user):
        return reverse("wjs_review_list")
    elif permissions.has_reviewer_role(journal, user):
        return reverse("wjs_review_reviewer_pending")
    elif permissions.has_author_role(journal, user):
        return reverse("wjs_review_author_pending")
    else:
        return reverse("website_index")
