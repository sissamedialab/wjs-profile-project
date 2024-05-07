from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from plugins.typesetting.models import TypesettingAssignment
from review.models import ReviewAssignment

from wjs.jcom_profile import permissions as base_permissions

if TYPE_CHECKING:
    from .models import ArticleWorkflow

Account = get_user_model()


def has_section_editor_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the given user has the section editor role for the journal associated with the given ArticleWorkflow.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has section editor role for the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_section_editor_role(instance.article.journal, user)


def has_editor_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if a user has an editor role for a specific article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has an editor role for the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_editor_role(instance.article.journal, user)


def has_director_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the given user has the director role for the article's journal.

    If the director is the author of the article, he can't be considered as a director for the article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the director role for the journal, False otherwise.
    :rtype: bool
    """
    if is_one_of_the_authors(instance, user):
        return False
    return base_permissions.has_director_role(instance.article.journal, user)


def has_admin_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is staff, also meaning EO.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the director role for the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_admin_role(instance.article.journal, user)


def has_reviewer_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user has the reviewer role for the article's journal.

    We don't look at the relation with the single article, just at AccountRole relation.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the reviewer role for the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_reviewer_role(instance.article.journal, user)


def has_author_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user has the author role for the article's journal.

    We don't look at the relation with the single article, just at AccountRole relation.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the author role for the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_author_role(instance.article.journal, user)


def has_eo_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user has the eo role for the article's journal.

    Article is actually ignored, but we need it for API compatibility.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the eo role, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_eo_role(user)


def has_eo_or_director_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the given user is part of the EO or has director role for the given journal.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the EO or the director role for the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_eo_role(user=user) or base_permissions.has_director_role(
        journal=instance.article.journal,
        user=user,
    )


def is_system(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Fake permission for system-managed transitions.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is None, False otherwise.
    :rtype: bool
    """
    return user is None


def has_any_editor_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user has any editor role on the journal linked to the given article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has any editor role on the journal, False otherwise.
    :rtype: bool
    """
    return base_permissions.has_any_editor_role(instance.article.journal, user)


def has_section_editor_or_reviewer_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user has section editor or reviewer role on the journal linked to the given article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has section editor or reviewer role on the journal, False otherwise.
    :rtype: bool
    """
    return has_section_editor_role_by_article(instance, user) or has_reviewer_role_by_article(instance, user)


def is_article_reviewer(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is one of reviewers of the article (eg: a ReviewAssignment exists).

    We don't look at the state of the assignment: we consider the user a reviewer for this paper as long as an
    assignment exists with this user as reviewer.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is assigned to the article as reviewer role, False otherwise.
    :rtype: bool
    """
    return ReviewAssignment.objects.filter(article=instance.article, reviewer=user).exists()


def is_article_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is an editor and has a valid :py:class:`EditorAssignment` to the given article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has section editor or reviewer role on the journal, False otherwise.
    :rtype: bool
    """
    return (
        has_any_editor_role_by_article(instance, user)
        and instance.article.editorassignment_set.filter(editor=user).exists()
    )


def is_article_author(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the correspondence author of the article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the correspondence author, False otherwise.
    :rtype: bool
    """
    return instance.article.correspondence_author == user


def is_one_of_the_authors(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Return True if the user is one of the authors or the correspondence author.

    Remember that, in J., it is not mandatory for the correspondence author to be one of the authors!

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is included in the article authors list or is the correspondence author, False otherwise.
    :rtype: bool
    """
    is_correspondence_author = instance.article.correspondence_author == user
    is_any_author = instance.article.authors.filter(pk=user.pk).exists()
    return is_correspondence_author | is_any_author


def is_special_issue_supervisor(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Return True if the user is either the editor, the director or the EO.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is included in the article authors list or is the correspondence author, False otherwise.
    :rtype: bool
    """
    return (
        is_article_editor(instance, user)
        or has_director_role_by_article(instance, user)
        or has_admin_role_by_article(instance, user)
    )


def can_assign_special_issue_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the Editor of the article or is the director of the journal or is part of the EO and if
    the article is assigned to a special issue.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the supervisor role on the special issue.
    :rtype: bool
    """
    is_article_special_issue = instance.article.issues.filter(issue_type__code="collection").exists()
    return is_special_issue_supervisor(instance, user) and is_article_special_issue


def has_typesetter_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user has the typesetter role for the journal of the given article.

    Since the pile of papers to take in charge is cross-journal, see also `has_typesetter_role_on_any_journal`.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the typesetter role for the journal of the given article.
    :rtype: bool
    """
    return user.check_role(instance.article.journal, "typesetter")


def is_article_typesetter(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the typesetter of the article.

    At the moment, like in the reviewer's method, I'm not checking for the article state.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the article typesetter
    :rtype: bool
    """
    return TypesettingAssignment.objects.filter(round__article=instance.article, typesetter=user).exists()
