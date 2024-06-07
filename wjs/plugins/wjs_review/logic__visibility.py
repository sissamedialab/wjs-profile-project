import dataclasses
from itertools import chain
from typing import Iterable, Optional

from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.db import models
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from review.models import (
    EditorAssignment,
    ReviewAssignment,
    ReviewRound,
    RevisionRequest,
)
from submission.models import Article

from wjs.jcom_profile import permissions as base_permissions

from . import permissions
from .models import (
    ArticleWorkflow,
    PastEditorAssignment,
    PermissionAssignment,
    WjsEditorAssignment,
)


@dataclasses.dataclass
class GrantPermissionOnReviewAssignment:
    user: Account
    """
    The user to grant permissions to.
    """
    review_assignment: ReviewAssignment
    """
    The review assignment instance to grant permissions on.
    """
    model = ReviewAssignment
    """
    Model to grant permission on.
    """

    def run(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
    ) -> PermissionAssignment:
        """
        Grant permissions to the user on the review assignment.

        :param permission_type: The permission set to grant.
        :type permission_type: PermissionAssignment.PermissionType
        :return: The created permission assignment.
        :rtype: PermissionAssignment
        """
        return PermissionAssignment.objects.create(
            user=self.user,
            target=self.review_assignment,
            permission=permission_type,
        )


@dataclasses.dataclass
class GrantPermissionOnRevisionRequest:
    user: Account
    """
    The user to grant permissions to.
    """
    revision_request: RevisionRequest
    """
    The revision request instance to grant permissions on.
    """
    model = RevisionRequest
    """
    Model to grant permission on.
    """

    def run(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
    ) -> PermissionAssignment:
        """
        Grant permissions to the user on the revision request.

        :param permission_type: The permission set to grant.
        :type permission_type: PermissionAssignment.PermissionType
        :return: The created permission assignment.
        :rtype: PermissionAssignment
        """
        return PermissionAssignment.objects.create(
            user=self.user,
            target=self.revision_request,
            permission=permission_type,
        )


@dataclasses.dataclass
class GrantPermissionOnEditorAssignment:
    user: Account
    """
    The user to grant permissions to.
    """
    editor_assignment: WjsEditorAssignment
    """
    The editor assignment instance to grant permissions on.
    """
    model = WjsEditorAssignment
    """
    Model to grant permission on.
    """

    def _get_past_review_rounds(self) -> Iterable[ReviewRound]:
        """
        Retrieve all past review rounds for the article of the editor assignment.

        If any past editor assignment exists, their review rounds are excluded.

        :return: The past review rounds.
        :rtype: Iterable[ReviewRound]
        """
        rounds = self.editor_assignment.article.reviewround_set.all()
        if PastEditorAssignment.objects.filter(article=self.editor_assignment.article).exists():
            past_assignments = PastEditorAssignment.objects.filter(article=self.editor_assignment.article)
            past_rr = [
                past_assignment.review_rounds.values_list("pk", flat=True) for past_assignment in past_assignments
            ]
            past_rounds = list(chain(*past_rr))
        rounds = rounds.exclude(pk__in=past_rounds)
        return rounds

    def _assign_review_rounds(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
    ):
        """
        Assign permissions to the user on all review assignments of the past review rounds of the article.
        """
        for review_round in self._get_past_review_rounds():
            for review_assignment in review_round.reviewassignment_set.all():
                GrantPermissionOnReviewAssignment(self.user, review_assignment).run(permission_type)

    def _assign_revision_request(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
    ):
        """
        Assign permissions to the user on all revision requests of the past review rounds of the article.
        """
        for review_round in self._get_past_review_rounds():
            for editor_revision_request in review_round.editorrevisionrequest_set.all():
                GrantPermissionOnReviewAssignment(self.user, editor_revision_request).run(permission_type)

    def run(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
        with_reviewers: bool = False,
        with_revisions: bool = False,
    ):
        """
        Grant permissions to the user on the editor assignment.

        Optionally, grant permissions on the review assignments and revision requests of the editor assignment.
        """
        PermissionAssignment.objects.create(
            user=self.user,
            target=self.editor_assignment,
            permission=permission_type,
        )
        if with_reviewers:
            self._assign_review_rounds()

        if with_revisions:
            self._assign_revision_request()


@dataclasses.dataclass
class GrantPermissionOnPastEditorAssignment(GrantPermissionOnEditorAssignment):
    user: Account
    """
    The user to grant permissions to.
    """
    editor_assignment: PastEditorAssignment
    """
    The editor assignment instance to grant permissions on.
    """
    model = PastEditorAssignment
    """
    Model to grant permission on.
    """

    def _get_past_review_rounds(self) -> Iterable[ReviewRound]:
        """
        Retrieve all review rounds for the past editor assignment.

        :return: The past review rounds.
        :rtype: Iterable[ReviewRound]
        """
        return self.editor_assignment.review_rounds.all()


@dataclasses.dataclass
class GrantPermissionOnReviewRound:
    user: Account
    """
    The user to grant permissions to.
    """
    review_round: ReviewRound
    """
    The review round instance to grant permissions on.
    """
    model = ReviewRound
    """
    Model to grant permission on.
    """

    def run(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
        # FIXME: decide how to handle these options
        # should they be boolean or a list of review rounds / objects to enable? Probably both
        with_reviewers: bool = False,
        with_revisions: bool = False,
    ):
        """
        Grant permissions to the user on the review round.

        :param permission_type: The permission set to grant.
        :type permission_type: PermissionAssignment.PermissionType
        :return: The created permission assignment.
        :rtype: PermissionAssignment
        """
        return PermissionAssignment.objects.create(
            user=self.user,
            target=self.review_round,
            permission=permission_type,
        )


@dataclasses.dataclass
class GrantPermissionDispatcher:
    user: Account
    object_type: str
    object_id: int

    _granters = {
        "editorassignment": GrantPermissionOnEditorAssignment,
        "reviewassignment": GrantPermissionOnReviewAssignment,
        "revisionrequest": GrantPermissionOnRevisionRequest,
        "pasteditorassignment": GrantPermissionOnPastEditorAssignment,
        "reviewround": GrantPermissionOnReviewRound,
    }

    def run(
        self,
        permission_type: PermissionAssignment.PermissionType = PermissionAssignment.PermissionType.ALL,
        options: Optional[dict[str, str]] = None,
    ):
        """
        Dispatch the permission assignment based on the instance type.

        :param permission_type: The permission set to grant.
        :type permission_type: PermissionAssignment.PermissionType
        :param options: Keyword arguments to pass to the granter.
        :type options: Optional[dict[str, str]]
        """
        granter = self._granters[self.object_type]
        instance = granter.model.objects.get(pk=self.object_id)
        return granter(self.user, instance).run(permission_type=permission_type, **(options or {}))


@dataclasses.dataclass
class BasePermissionChecker:
    user: Account
    workflow: ArticleWorkflow
    instance: models.Model

    def check_permission_object(self, permission_type: PermissionAssignment.PermissionType = "") -> Optional[bool]:
        """
        Check if the user has a custom permission for :py:attr:`instance` object.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
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
            return custom_permission.match_permission(permission_type)
        except PermissionAssignment.DoesNotExist:
            return None

    def check(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        Permissions can be granted by default logic, or by assigning a custom permission to the user.

        If custom permission check succeeds, the default permission check is skipped and check returns True.
        If custom permission check fails (explicitly denied), the default permission check is skipped and check
        returns False.
        Else we rely on the default permission check.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if custom_passed := self.check_permission_object(permission_type):
            return True
        if custom_passed is False:
            return False
        default_passed = self.check_default(permission_type)
        return bool(default_passed)


@dataclasses.dataclass
class SuperUserPermissionChecker(BasePermissionChecker):
    def check(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        As super user, the user has access to all objects.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        return base_permissions.has_admin_role(self.workflow.article.journal, self.user)


@dataclasses.dataclass
class DirectorPermissionChecker(BasePermissionChecker):
    def check(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance`.

        As a director, the user has access to all objects for their journal, except for articles they authored.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if self.workflow.article.authors.filter(pk=self.user.pk).exists():
            return False
        return base_permissions.has_director_role(self.workflow.article.journal, self.user)


@dataclasses.dataclass
class EditorPermissionChecker(BasePermissionChecker):
    def check_default(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        If the user is the editor assigned to the instance we grant permission.

        This allows to let the editor access their own assignment data even after they have been removed from the role,
        because all linked objects are explicitly assigned to them. The deleted assignment is replaced by a
        PastEditorAssignment, which again is linked to the editor.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, Article):
            current_editor = WjsEditorAssignment.objects.get_all(self.instance).filter(editor=self.user).exists()
            past_editor = PastEditorAssignment.objects.filter(editor=self.user, article=self.instance).exists()
            return current_editor or past_editor
        if isinstance(self.instance, EditorAssignment):
            return self.instance.editor == self.user
        if isinstance(self.instance, RevisionRequest):
            return self.instance.editor == self.user
        if isinstance(self.instance, ReviewAssignment):
            return self.instance.editor == self.user
        if isinstance(self.instance, PastEditorAssignment):
            return self.instance.editor == self.user


@dataclasses.dataclass
class TypeSetterPermissionChecker(BasePermissionChecker):
    def check_default(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        If the user is the typesetter assigned to the instance we grant permission.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
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


@dataclasses.dataclass
class AuthorPermissionChecker(BasePermissionChecker):
    def check_default(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        If the user is one of the authors we grant permission.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, GalleyProofing):
            return self.instance.proofreader == self.user
        if isinstance(self.instance, ArticleWorkflow):
            return self.instance.article.authors.filter(pk=self.user.pk).exists()
        if isinstance(self.instance, Article):
            return self.instance.authors.filter(pk=self.user.pk).exists()
        if isinstance(self.instance, RevisionRequest):
            return self.instance.article.authors.filter(pk=self.user.pk).exists()


@dataclasses.dataclass
class ReviewerPermissionChecker(BasePermissionChecker):
    def check_default(self, permission_type: PermissionAssignment.PermissionType = "") -> bool:
        """
        Check if the user has the permission to access :py:attr:`instance` by default.

        If the user is one of the reviewers we grant permission.

        :param permission_type: The permission set to check for.
        :type permission_type: PermissionAssignment.PermissionType
        :return: True if the user has the permission, False otherwise.
        :rtype: bool
        """
        if isinstance(self.instance, ReviewAssignment):
            return self.instance.reviewer == self.user
        if isinstance(self.instance, ArticleWorkflow):
            return self.instance.article.reviewassignment_set.filter(reviewer=self.user).exists()
        if isinstance(self.instance, Article):
            return self.instance.reviewassignment_set.filter(reviewer=self.user).exists()


class PermissionChecker:
    # except for special issue supervisor and authors, where we check directly in the permission function the
    # relationship between the user and the article, all other roles checking function are broad by role only,
    # instead of checking the specific permission, because we must take custom permissions into account, which
    # would be ruled out by a specific permission check.
    _permission_classes = {
        permissions.has_admin_role_by_article: SuperUserPermissionChecker,
        permissions.has_director_role_by_article: DirectorPermissionChecker,
        permissions.is_special_issue_editor: EditorPermissionChecker,  # TODO: TBD
        permissions.has_typesetter_role_by_article: TypeSetterPermissionChecker,
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
    ) -> bool:
        allowed = False
        for checker_function, checker_class in self._permission_classes.items():
            if checker_function(workflow, user):
                checker = checker_class(user=user, workflow=workflow, instance=instance)
                allowed |= checker.check(permission_type)

        return allowed
