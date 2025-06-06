from logging import getLogger
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.utils.timezone import now
from journal.models import Journal
from plugins.typesetting.models import TypesettingAssignment
from review.models import ReviewAssignment

from wjs.jcom_profile import constants
from wjs.jcom_profile import permissions as base_permissions

logger = getLogger(__name__)
if TYPE_CHECKING:
    from .models import ArticleWorkflow, Message, WorkflowReviewAssignment

Account = get_user_model()


def main_role_by_article(article: "ArticleWorkflow", user: Account) -> str:
    """
    Return the main role of the user.

    :param article: An instance of the ArticleWorkflow class.
    :type article: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: The main role of the user.
    :rtype: str
    """
    if base_permissions.has_eo_role(user):
        return constants.EO_GROUP
    if is_article_editor(article, user):
        # NB: technically, the editor has the "section-editor" role, but we report "editor"
        # because it's more user-friendly.
        return constants.EDITOR_ROLE
    if has_director_role_by_article(article, user):
        # We do have both a "director" and "main director" roles, but they are functionally equivalent
        return constants.DIRECTOR_ROLE
    if is_article_typesetter(article, user):
        return constants.TYPESETTER_ROLE
    if is_article_reviewer(article, user):
        return constants.REVIEWER_ROLE
    if is_one_of_the_authors(article, user):
        return constants.AUTHOR_ROLE

    logger.error(
        f"Function permissions.main_role_by_article() called for an Account ({user.id})"
        " that has no role wrt to AW {article.id} ({article.article.id})",
    )
    return ""


def main_role_by_assignment(assignment: "WorkflowReviewAssignment", user: Account) -> str:
    """
    Return the main role of the user.

    :param assignment: An instance of the ArticleWorkflow class.
    :type assignment: WjsReviewAssignment

    :param user: The user to check for role.
    :type user: Account

    :return: The main role of the user.
    :rtype: str
    """
    if assignment.editor == user:
        return constants.SECTION_EDITOR_ROLE
    elif assignment.reviewer == user:
        return constants.REVIEWER_ROLE
    else:
        return main_role_by_article(assignment.article.articleworkflow, user)


def can_see_other_user_name(instance: "ArticleWorkflow", actor: Account, target: Account) -> bool:
    """
    Check if a user (the actor) can see another user's name (the target).

    :param instance: The article(workflow) wrt which users roles are computed.
    :type instance: ArticleWorkflow

    :param actor: The user to check permissions for (who is doing the seeing).
    :type actor: Account

    :param target: The target user (who is been seen).
    :type target: Account

    :return: True if actor can see the target's name, False otherwise.
    :rtype: bool
    """
    target_is_author = is_one_of_the_authors(instance, target)
    actor_is_editor = is_article_editor(instance, actor)
    # NB: order is important! If ed was also reviewer, the editor-role is more important.
    if actor_is_editor and target_is_author:
        return True

    actor_is_author = is_one_of_the_authors(instance, actor)
    target_is_editor = is_article_editor(instance, target)
    if actor_is_author and target_is_editor:
        return False

    actor_is_reviewer = is_article_reviewer(instance, actor)
    if actor_is_reviewer and target_is_author:
        return False

    target_is_reviewer = is_article_reviewer(instance, target)
    if actor_is_author and target_is_reviewer:
        return False

    actor_is_typesetter = is_article_typesetter(instance, actor)
    if actor_is_typesetter and target_is_author:  # noqa: SIM103
        return False

    return True


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


def has_main_director_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the given user has the main director role for the article's journal.

    If the director is the author of the article, he can't be considered as a director for the article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the main director role for the journal, False otherwise.
    :rtype: bool
    """
    if is_one_of_the_authors(instance, user):
        return False
    return base_permissions.has_main_director_role(instance.article.journal, user)


def has_any_director_role_by_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the given user has the director or main director role for the article's journal.

    If the director is the author of the article, he can't be considered as a director for the article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the director or main director role for the journal, False otherwise.
    :rtype: bool
    """
    if is_one_of_the_authors(instance, user):
        return False
    return base_permissions.has_any_director_role(instance.article.journal, user)


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


def is_assignment_reviewer(instance: "WorkflowReviewAssignment", user: Account) -> bool:
    """
    Check if the user is the is assignment reviewer.

    :param instance: An instance of the WorkflowReviewAssignment class.
    :type instance: WorkflowReviewAssignment

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the assignment reviewer, False otherwise.
    :rtype: bool
    """
    return instance.reviewer == user


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
    Check if the user is an editor and has a valid :py:class:`WjsEditorAssignment` to the given article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is a curent editor for the given article, False otherwise.
    :rtype: bool
    """
    from .models import WjsEditorAssignment

    return (
        has_any_editor_role_by_article(instance, user)
        and WjsEditorAssignment.objects.get_all(instance).filter(editor=user).exists()
    )


def is_past_article_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is an editor and has :py:class:`PastEditorAssignment` to the given article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is a past editor for the given article, False otherwise.
    :rtype: bool
    """
    from .models import PastEditorAssignment

    return (
        has_any_editor_role_by_article(instance, user)
        and PastEditorAssignment.objects.filter(article=instance.article, editor=user).exists()
    )


def is_article_author(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the Corresponding author of the article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the Corresponding author, False otherwise.
    :rtype: bool
    """
    return instance.article.correspondence_author == user


def is_article_author_and_paper_can_go_rfp(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the Corresponding author and if the article can transition into READY_FOR_PUBLI CATION.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the Corresponding author, False otherwise.
    :rtype: bool
    """
    return instance.can_be_set_rfp() and is_article_author(instance, user)


def is_article_typesetter_and_paper_can_go_rfp(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the typesetter and if the article can transition into READY_FOR_PUBLICATION.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the Corresponding author, False otherwise.
    :rtype: bool
    """
    # TODO: consider rfc with the above method `is_article_author_and_paper_can_go_rfp`
    return instance.can_be_set_rfp() and is_article_typesetter(instance, user)


def is_one_of_the_authors(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Return True if the user is one of the authors or the Corresponding author.

    Remember that, in J., it is not mandatory for the Corresponding author to be one of the authors!

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is included in the article authors list or is the Corresponding author, False otherwise.
    :rtype: bool
    """
    is_correspondence_author = instance.article.correspondence_author == user
    is_any_author = instance.article.authors.filter(pk=user.pk).exists()
    return is_correspondence_author | is_any_author


def is_article_manager(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is responsible for managing any phase of the article review / production

    User is supervisor (EO, director, editor of a SI - see is_article_supervisor) or typesetter or editor.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has section editor or reviewer role on the journal, False otherwise.
    :rtype: bool
    """
    return (
        is_article_supervisor(instance, user)
        or is_article_typesetter(instance, user)
        or is_article_editor(instance, user)
    )


def is_article_supervisor(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user can manage article permissions and assignments (article supervisor).

    This is available to:

    - the EO
    - the director
    - the editor if the article is part of a special issue

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user has the supervisor role on the special issue.
    :rtype: bool
    """
    return (
        is_special_issue_editor(instance, user)
        or has_director_role_by_article(instance, user)
        or has_admin_role_by_article(instance, user)
        or base_permissions.has_eo_role(user)
    )


def is_special_issue_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the editor of the special issue associated with the article.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the editor of the special issue.
    :rtype: bool
    """

    is_special_issue_editor = instance.article.issues.filter(managing_editors=user).exists()
    has_editor_role = base_permissions.has_section_editor_role(instance.article.journal, user)
    return is_special_issue_editor and has_editor_role


def is_any_special_issue_editor(journal: Journal, user: Account) -> bool:
    """
    Check if the user is the editor of any special issue.

    :param journal: An instance of the Journal to check for special issues
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the editor of any special issue.
    :rtype: bool
    """
    return journal.issues.filter(managing_editors=user).exists()


def is_any_open_special_issue_editor(journal: Journal, user: Account) -> bool:
    """
    Check if the user is the editor of any unpublished special issue.

    :param journal: An instance of the Journal to check for special issues
    :type journal: Journal

    :param user: The user to check for role.
    :type user: Account

    :return: True if the user is the editor of any special issue.
    :rtype: bool
    """
    return journal.issues.filter(managing_editors=user, date__lte=now()).exists()


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
    return user.check_role(instance.article.journal, "typesetter", staff_override=False)


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


def is_article_typesetter_or_eo(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the typesetter or eo.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow
    :param user: The user to check for role.
    :type user: Account
    :return: True if the user is the article typesetter
    :rtype: bool
    """
    return base_permissions.has_eo_role(user) or is_article_typesetter(instance, user)


def is_article_pure_editor_or_eo(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the editor or eo.

    In case user is editor, it must not be a reviewer also. This function should be used to tweak the UI for the editor
    when they choose to review the article themselves, not for real permission checking.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow
    :param user: The user to check for role.
    :type user: Account
    :return: True if the user is the article editor or eo
    :rtype: bool
    """
    manager = is_article_editor(instance, user) or is_article_supervisor(instance, user)
    return manager and not is_article_reviewer(instance, user)


def is_article_editor_or_eo(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is the editor or eo.

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow
    :param user: The user to check for role.
    :type user: Account
    :return: True if the user is the article editor
    :rtype: bool
    """
    return is_article_editor(instance, user) or is_article_supervisor(instance, user)


def is_person_working_on_article(instance: "ArticleWorkflow", user: Account) -> bool:
    """
    Check if the user is a person working on the article (editor, reviewer, staff).

    :param instance: An instance of the ArticleWorkflow class.
    :type instance: ArticleWorkflow
    :param user: The user to check for role.
    :type user: Account
    :return: True if the user is the article typesetter
    :rtype: bool
    """
    return (
        is_article_editor(instance, user)
        or is_article_supervisor(instance, user)
        or is_article_reviewer(instance, user)
    )


def can_see_reviewer_name(assignment: "WorkflowReviewAssignment", user: Account) -> bool:
    """
    Check if the user can see the reviewer name.

    :param assignment: An instance of the WorkflowReviewAssignment class.
    :type assignment: WorkflowReviewAssignment
    :param user: The user to check for role.
    :type user: Account
    :return: True if the user can see the reviewer name
    :rtype: bool
    """
    from .logic__visibility import PermissionChecker
    from .models import PermissionAssignment

    has_editor_role = is_article_pure_editor_or_eo(assignment.article.articleworkflow, user)

    return has_editor_role and PermissionChecker()(
        assignment.article.articleworkflow,
        user,
        assignment,
        permission_type=PermissionAssignment.PermissionType.ALL,
    )


def can_edit_note(user: Account, message: "Message"):
    """
    Check if the user can edit the note.

    This is only possible if the user is the note actor or they are both part of the EO.

    :param user: Current user
    :type user: Account
    :param message: Note instance
    :type message: models.Message
    :return: True if the user can edit the note, False otherwise.
    :rtype: bool
    """
    current_user_is_eo = base_permissions.has_eo_role(user)
    actor_is_eo = base_permissions.has_eo_role(message.actor)
    user_responsible_for_note = message.actor == user or (current_user_is_eo and actor_is_eo)
    return user_responsible_for_note
