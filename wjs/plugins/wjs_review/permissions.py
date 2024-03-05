from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from review.models import ReviewAssignment

from wjs.jcom_profile.permissions import is_eo as base_is_eo

if TYPE_CHECKING:
    from .models import ArticleWorkflow

Account = get_user_model()


def is_section_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "section-editor")


def is_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "editor")


def is_director(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "director")


def is_admin(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True is the user is staff, also meaning EO."""
    return user.is_staff


def is_reviewer(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True if the user has the "reviewer" role for this journal.

    We don't look at the relation with the single article.
    """
    return user.check_role(instance.article.journal, "reviewer")


def is_article_reviewer(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True if the user is one of reviewers of the article.

    We don't look at the state of the assignment: we consider the user a reviewer for this paper as long as an
    assignment exists with this user as reviewer.
    """
    return ReviewAssignment.objects.filter(article=instance.article, reviewer=user).exists()


def is_author(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True if the user has the "author" role for this journal.

    We don't look at the relation with the single article.
    """
    return user.check_role(instance.article.journal, "author")


def is_section_editor_or_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return is_section_editor(instance, user) or is_editor(instance, user)


def is_section_editor_or_reviewer(instance: "ArticleWorkflow", user: Account) -> bool:
    return is_section_editor(instance, user) or is_reviewer(instance, user)


def is_article_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return (
        is_section_editor(instance, user) or is_editor(instance, user)
    ) and instance.article.editorassignment_set.filter(editor=user).exists()


def is_article_author(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True only is the user is the correspondence author.

    I.e. we don't look at the full authors list.
    """
    return instance.article.correspondence_author == user


def is_system(instance: "ArticleWorkflow", user: Account) -> bool:
    """Fake permission for system-managed transitions."""
    return user is None


def is_eo(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True only is the user is part of the EO.

    Wraps :py:func:`wjs.jcom_profile.permissions.is_eo`, needed to accept the instance parameter.
    """
    return base_is_eo(user)


def is_one_of_the_authors(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True if the user is one of the authors or the correspondence author.

    Remember that, in J., it is not mandatory for the correspondence author to be one of the authors!
    """

    is_correspondence_author = instance.article.correspondence_author == user
    is_any_author = instance.article.authors.filter(pk=user.pk).exists()
    return is_correspondence_author | is_any_author
