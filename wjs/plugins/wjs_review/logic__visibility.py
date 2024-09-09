import dataclasses
from typing import Optional

from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.db import models
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from review.models import EditorAssignment, ReviewAssignment, RevisionRequest
from submission.models import Article

from wjs.jcom_profile import permissions as base_permissions

from . import permissions
from .models import (
    ArticleWorkflow,
    PastEditorAssignment,
    PermissionAssignment,
    WjsEditorAssignment,
)


def get_recipient_label(workflow: ArticleWorkflow, user: Account, recipient: Account) -> str:
    """
    Get the label for the recipient of a message.

    :param workflow: ArticleWorkflow object
    :param user: User sending the message
    :param recipient: User receiving the message
    :return:
    """
    real_name = str(recipient)
    if permissions.can_see_other_user_name(instance=workflow, sender=recipient, recipient=user):
        return real_name
    else:
        if permissions.is_article_typesetter(instance=workflow, user=recipient):
            return "typesetter"
        elif permissions.is_article_editor(instance=workflow, user=recipient):
            return "editor"
        else:
            return real_name


@dataclasses.dataclass
class BasePermissionChecker:
    """Machinery to check permissions.

    We should be able to manage what someone can see and to what extent.
    Here follows a brief review of the "what" the "who" and the "how".

    ### What
    As of June '24, we have the following "items" that we must be able to manage:
    - Article/ArticleWorkflow
      - metadata: covered by the (primary) permission
      - initial author cover letter: covered by the secondary permission

    - (Wjs)EditorAssignment - assignment metadata (editor name and dates); covered by the (primary) permission

    - ReviewAssignment - reviewer report (permission)

    - (Editor)RevisionRequest
      - editor report (permission)
      - author cover letter that replies to this revision request (secondary_permission)

    ### Who
    By default, these are (roughly) the permissions of the roles:
    - EO - sees everything

    - Director - same as EO, except for papers they authored

    - Editor - sees everything related to the papers' review rounds where they are/was the editor

    - Reviewer - sees only their stuff for the papers they have been assigned, but not the authors' names

    - Typesetter - sees everything when the paper is RFT and only the papers they have in charge otherwise

    - Author - sees only their stuff and the editor's report (no editor name, or reviewers' reports)


    ### How (permission type)
    We define tre (primary) permission types or set:
    - ALL (this "includes" NO_NAMES permission; in this sense, the permissions are a set or a hierarchy)
    - NO_NAMES: the recipient can only see the documents, but not the name of the people involved
      E.g.: authors can see the editor's report, but not the editor's name
            reviewers can see the paper's metadata and the author cover letter, but not the authors' names
    - DENY

    For some of the items, only two permissions make sense: the BinaryPermission ALL/DENY

    ### Structure
    We have a PermissionChecker that can check the default permission for a user on an object
    (primary or secondary permissions) or any custom permission.
    Custom permissions are explicit deviations from the default and are managed by existence of
    PermissionAssignment objects that link the object, the user and the "how" (see the model).

    :param user: the "who"
    :type user: Account

    :param workflow: the paper onto which we are working
    :type workflow: ArticleWorkflow

    :param instance: the "what"
    :type instance : Model (anyone of the models above)

    """

    user: Account
    workflow: ArticleWorkflow
    instance: models.Model

    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        This method is called if no custom permission is set for the user.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        raise NotImplementedError

    def check_permission_object(
        self, permission_type: PermissionAssignment.PermissionType = "", secondary_permission: bool = False
    ) -> Optional[bool]:
        """
        Check if the user has a custom permission for :py:attr:`instance` object.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :return: True if the user has the permission, False if has an explicit denied permission, None if no custom
                  permission is set.
        :rtype: bool
        """
        try:
            ct = ContentType.objects.get_for_model(self.instance)

            custom_permission = PermissionAssignment.objects.filter(
                user=self.user,
                content_type=ct,
                object_id=self.instance.pk,
            ).get()
            if secondary_permission:
                return custom_permission.match_secondary_permission(permission_type)
            else:
                return custom_permission.match_permission(permission_type)
        except PermissionAssignment.DoesNotExist:
            return None

    def check(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        Permissions can be granted by default logic, or by assigning a custom permission to the user.

        If custom permission check succeeds, the default permission check is skipped and check returns True.
        If custom permission check fails (explicitly denied), the default permission check is skipped and check
        returns False.
        Else we rely on the default permission check.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used. Currently used for default permission check only.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if custom_passed := self.check_permission_object(permission_type, secondary_permission):
            return True
        if custom_passed is False:
            return False
        default_passed = self.check_default(permission_type, secondary_permission, review_round)
        return bool(default_passed)


@dataclasses.dataclass
class SuperUserPermissionChecker(BasePermissionChecker):
    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        As super user, the user has access to all objects.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        return base_permissions.has_admin_role(self.workflow.article.journal, self.user)


@dataclasses.dataclass
class DirectorPermissionChecker(BasePermissionChecker):
    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        As a director, the user has access to all objects for their journal, except for articles they authored.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if self.workflow.article.authors.filter(pk=self.user.pk).exists():
            return False
        return base_permissions.has_director_role(self.workflow.article.journal, self.user)


@dataclasses.dataclass
class EditorPermissionChecker(BasePermissionChecker):
    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        This allows to let the editor access their own assignment data even after they have been removed from the role,
        because all linked objects are explicitly assigned to them. The deleted assignment is replaced by a
        PastEditorAssignment, which again is linked to the editor.

        By default editor has access to all the information on all the objects linked to an article they are assigned
        to or they were assigned to in the past. If one wants to remove permission, custom permissions with DENY
        permission type must be used.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, Article):
            current_editor = WjsEditorAssignment.objects.get_all(self.instance).filter(editor=self.user).exists()
            if current_editor:
                return True
            past_editor = PastEditorAssignment.objects.filter(editor=self.user, article=self.instance).exists()
            return past_editor
        if isinstance(self.instance, ArticleWorkflow):
            current_editor = WjsEditorAssignment.objects.get_all(self.instance).filter(editor=self.user).exists()
            if current_editor:
                return True
            past_editor = PastEditorAssignment.objects.filter(editor=self.user, article=self.instance.article).exists()
            return past_editor
        if isinstance(self.instance, EditorAssignment):
            return self.instance.editor == self.user
        if isinstance(self.instance, RevisionRequest):
            return self.instance.editor == self.user
        if isinstance(self.instance, ReviewAssignment):
            return self.instance.editor == self.user
        if isinstance(self.instance, PastEditorAssignment):
            return self.instance.editor == self.user
        return False


@dataclasses.dataclass
class TypesetterPermissionChecker(BasePermissionChecker):
    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        By default typesetter has access to all the information on all the objects linked to an article they are
        assigned to or in READY_FOR_TYPESETTER state. If one wants to remove permission, custom permissions with DENY
        permission type must be used.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, ArticleWorkflow):
            if self.instance.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER:
                return True
            if self.instance.article.typesettinground_set.filter(
                typesettingassignment__typesetter_id=self.user.pk
            ).exists():
                return True
            return False
        if isinstance(self.instance, Article):
            if self.instance.articleworkflow.state == ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER:
                return True
            if self.instance.typesettinground_set.filter(typesettingassignment__typesetter_id=self.user.pk).exists():
                return True
            return False
        if isinstance(self.instance, TypesettingAssignment):
            return self.instance.typesetter == self.user
        if isinstance(self.instance, GalleyProofing):
            return self.instance.manager == self.user
        return False


@dataclasses.dataclass
class AuthorPermissionChecker(BasePermissionChecker):
    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        By default author has access to its articles, proofing for their articles; for revision requests, they have
        NO_NAMES permission.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, GalleyProofing):
            return self.instance.proofreader == self.user
        if isinstance(self.instance, ArticleWorkflow):
            return permissions.is_one_of_the_authors(self.instance, self.user)
        if isinstance(self.instance, Article):
            return permissions.is_one_of_the_authors(self.instance.articleworkflow, self.user)
        if isinstance(self.instance, RevisionRequest):
            is_an_author = permissions.is_one_of_the_authors(self.instance.articleworkflow, self.user)
            return is_an_author and permission_type == PermissionAssignment.PermissionType.NO_NAMES
        return False


@dataclasses.dataclass
class ReviewerPermissionChecker(BasePermissionChecker):
    def _check_assignment_by_round(self, article: Article, review_round: int) -> bool:
        """Check if reviewer for the given round round number."""
        if review_round is None:
            # In this case we are only interested if the user has been reviewer at any time for current or past RR
            return article.reviewassignment_set.filter(reviewer=self.user).exists()
        elif review_round == 0:
            # In this case we want to check permissions on current review round
            return article.reviewassignment_set.filter(
                reviewer=self.user, review_round=article.current_review_round_object()
            ).exists()
        else:
            # To check permissions for specific review rounds
            return article.reviewassignment_set.filter(
                reviewer=self.user, review_round__round_number=review_round
            ).exists()

    def check_default(
        self,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        By default reviewer has access to its assignment; for articles, they have
        NO_NAMES permission as main permission (article metadata, files) and ALL for secondary permissions
        (comments_to_editor); for past review rounds they have NO_NAMES permission to article files, no access
         for secondary permissions (comments_to_editor).

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, ReviewAssignment):
            return self.instance.reviewer == self.user
        if isinstance(self.instance, ArticleWorkflow):
            is_assignee = self._check_assignment_by_round(article=self.instance.article, review_round=review_round)
            if secondary_permission:
                return is_assignee
            else:
                return is_assignee and permission_type == PermissionAssignment.PermissionType.NO_NAMES
        if isinstance(self.instance, Article):
            is_assignee = self._check_assignment_by_round(article=self.instance, review_round=review_round)
            if secondary_permission:
                return is_assignee
            else:
                return is_assignee and permission_type == PermissionAssignment.PermissionType.NO_NAMES
        if isinstance(self.instance, RevisionRequest):
            is_assignee = self.instance.article.reviewassignment_set.filter(reviewer=self.user).exists()
            if secondary_permission:
                return False
            return is_assignee and permission_type == PermissionAssignment.PermissionType.NO_NAMES
        return False


class PermissionChecker:
    # except for special issue supervisor and authors, where we check directly in the permission function the
    # relationship between the user and the article, all other roles checking function are broad by role only,
    # instead of checking the specific permission, because we must take custom permissions into account, which
    # would be ruled out by a specific permission check.
    _permission_classes = {
        permissions.has_admin_role_by_article: SuperUserPermissionChecker,
        permissions.has_director_role_by_article: DirectorPermissionChecker,
        permissions.is_special_issue_editor: EditorPermissionChecker,  # TODO: TBD
        permissions.has_typesetter_role_by_article: TypesetterPermissionChecker,
        permissions.has_section_editor_role_by_article: EditorPermissionChecker,
        permissions.has_reviewer_role_by_article: ReviewerPermissionChecker,
        permissions.is_one_of_the_authors: AuthorPermissionChecker,
    }

    def __call__(
        self,
        workflow: ArticleWorkflow,
        user: Account,
        instance: models.Model,
        permission_type: PermissionAssignment.PermissionType = "",
        secondary_permission: bool = False,
        default_permissions: bool = False,
        review_round: Optional[int] = None,
    ) -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        :param workflow: Base ArticleWorkflow object related to the instance parameter.
        :type workflow: ArticleWorkflow
        :param user: User to check permission for.
        :type user: Account
        :param instance: The actual object we want to check permission on.
        :type instance: models.Model,
        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :param secondary_permission: Check secondary set of permissions.
        :type secondary_permission: bool
        :param default_permissions: Check only default permissions.
        :type default_permissions: bool
        :param review_round: Check permission for a specific review round. If 0 current review round is used,
            if None review round check is not used.
        :type review_round: Optional[int]
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        has_the_permission = False
        for checker_function, checker_class in self._permission_classes.items():
            if checker_function(workflow, user):
                checker = checker_class(user=user, workflow=workflow, instance=instance)
                if default_permissions:
                    has_the_permission |= checker.check_default(
                        permission_type, secondary_permission, review_round=review_round
                    )
                else:
                    has_the_permission |= checker.check(
                        permission_type, secondary_permission, review_round=review_round
                    )

        return has_the_permission
