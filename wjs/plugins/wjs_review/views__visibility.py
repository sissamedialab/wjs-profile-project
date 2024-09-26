from operator import attrgetter
from typing import TYPE_CHECKING, List, Type, Union, cast

from core.models import Account
from django import forms
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.forms import formset_factory
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import FormView

from .custom_types import (
    PermissionConfiguration,
    PermissionInitial,
    PermissionTargetObject,
)
from .forms__visibility import BaseUserPermissionFormSet, UserPermissionsForm
from .logic__visibility import PermissionChecker
from .models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    PermissionAssignment,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from .permissions import is_article_editor, is_article_supervisor
from .views import BaseRelatedViewsMixin

if TYPE_CHECKING:
    from .custom_types import BreadcrumbItem


class EditUserPermissions(BaseRelatedViewsMixin, FormView):
    base_title = _("Set visibility rights for")
    model = ArticleWorkflow
    template_name = "wjs_review/edit_permissions/assign_permission.html"
    context_object_name = "workflow"
    redirect = False

    def load_initial(self, request, *args, **kwargs):
        super().load_initial(request, *args, **kwargs)
        self.workflow = self.model.objects.get(pk=kwargs["pk"])
        self.user = Account.objects.get(pk=kwargs["user_id"])

    def test_func(self):
        return is_article_editor(self.workflow, self.request.user) or is_article_supervisor(
            self.workflow, self.request.user
        )

    @property
    def title(self):
        return f"{self.base_title} {self.user}"

    @property
    def breadcrumbs(self) -> List["BreadcrumbItem"]:
        from .custom_types import BreadcrumbItem

        return [
            BreadcrumbItem(url=reverse("wjs_article_details", kwargs={"pk": self.workflow.pk}), title=self.workflow),
            BreadcrumbItem(
                url=reverse("wjs_select_reviewer", kwargs={"pk": self.workflow.pk}), title=self.title, current=True
            ),
        ]

    def get_form_class(self) -> Type[Union[forms.Form, forms.BaseFormSet]]:
        return formset_factory(
            UserPermissionsForm,
            extra=0,
            can_delete_extra=False,
            formset=BaseUserPermissionFormSet,
        )

    def _get_article_objects(self) -> list[PermissionTargetObject]:
        """
        Get all objects that need permissions assigned.

        Returned objects are sorted by round number and filtered by current user role.

        If EO / Director -> all objects are returned
        If Editor -> only objects linked to the review rounds assigned to the editor are returned

        :return: List of objects that need permissions assigned
        :rtype: list[PermissionTargetObject]
        """
        workflow_type = ContentType.objects.get_for_model(self.workflow)
        editor_revisions = EditorRevisionRequest.objects.filter(article=self.workflow.article).exclude(
            article__correspondence_author=self.user
        )
        editor_revisions_type = ContentType.objects.get_for_model(EditorRevisionRequest)
        editor_decisions = EditorDecision.objects.filter(workflow=self.workflow).exclude(
            review_round__article__correspondence_author=self.user
        )
        editor_decisions_type = ContentType.objects.get_for_model(EditorDecision)
        review_assignments = WorkflowReviewAssignment.objects.filter(article=self.workflow.article).exclude(
            reviewer=self.user
        )
        review_assignments_type = ContentType.objects.get_for_model(WorkflowReviewAssignment)
        if is_article_editor(self.workflow, self.request.user):
            assignment = WjsEditorAssignment.objects.get_current(self.workflow)
            review_assignments = review_assignments.filter(review_round__in=assignment.review_rounds.all())
            editor_revisions = editor_revisions.filter(review_round__in=assignment.review_rounds.all())

        target_objects = (
            [
                PermissionTargetObject(
                    object_type=workflow_type.pk,
                    object=self.workflow,
                    round=1,  # "Fake" review round to tag the initial submission, to order it before all the other
                    author_notes=True,
                    date_reference=self.workflow.article.date_submitted or self.workflow.article.date_created,
                )
            ]
            + [
                PermissionTargetObject(
                    object_type=editor_decisions_type.pk,
                    object=obj,
                    round=obj.review_round.round_number,
                    date_reference=obj.created,
                )
                for obj in editor_decisions
            ]
            # We manage author notes (aka author cover letter) separately.
            # These info are stored in editor_revisions, but they are intended more
            # as the author's introduction to the next round/version,
            # and not as the author's answer to the revision request
            # Therefore we duplicate the object with author_notes flag, and move it to the next round
            # It will be rendered twice but using different set of fields
            + [
                PermissionTargetObject(
                    object_type=editor_revisions_type.pk,
                    object=obj,
                    round=obj.review_round.round_number + 1,
                    author_notes=True,
                    date_reference=obj.date_requested,
                )
                for obj in editor_revisions
            ]
            + [
                PermissionTargetObject(
                    object_type=review_assignments_type.pk,
                    object=obj,
                    round=obj.review_round.round_number,
                    date_reference=obj.date_requested,
                )
                for obj in review_assignments
            ]
        )
        # sorting by object type is mainly to provide data stability during tests
        return sorted(target_objects, key=attrgetter("round", "date_reference", "object_type"), reverse=True)

    def _check_current_permission(
        self,
        user: Account,
        obj: PermissionTargetObject,
        custom_permission: PermissionConfiguration,
        binary: bool = False,
    ) -> PermissionAssignment.PermissionType:
        """
        Check the current permission for the user on the object.

        Different permission types are checked separately to determine the permission level:

        - Custom permission
        - Default permission for "all" levels
        - Default permission for "no names" levels (check is skipped if the permission is binary)

        :param user: User to check the permission for
        :type user: Account
        :param obj: Object to check the permission for
        :type obj: PermissionTargetObject
        :param custom_permission: Custom permission configuration
        :type custom_permission: PermissionConfiguration
        :param binary: If the permission is binary (when checking secondary permissions)
        :type binary: bool

        :return: Permission level for the user
        :rtype: PermissionAssignment.PermissionType
        """
        custom_permission_type = custom_permission.get((obj.object_type, obj.object.pk), None)
        if custom_permission_type is not None:
            return custom_permission_type
        default_all_permission = PermissionChecker()(
            self.workflow,
            user,
            obj.object,
            permission_type=PermissionAssignment.PermissionType.ALL,
            default_permissions=True,
            secondary_permission=binary,
        )
        if default_all_permission:
            return PermissionAssignment.PermissionType.ALL
        # if the permission is binary, we don't need to check the NO_NAMES permission
        if binary:
            return PermissionAssignment.PermissionType.DENY
        default_no_names_permission = PermissionChecker()(
            self.workflow,
            user,
            obj.object,
            permission_type=PermissionAssignment.PermissionType.NO_NAMES,
            default_permissions=True,
            secondary_permission=binary,
        )
        if default_no_names_permission:
            return PermissionAssignment.PermissionType.NO_NAMES
        return PermissionAssignment.PermissionType.DENY

    def get_initial(self) -> list[PermissionInitial]:
        """
        Get initial data for the form.

        :return: List of initial data for the form
        :rtype: list[PermissionInitial]
        """
        objects = self._get_article_objects()
        permissions: PermissionConfiguration = {
            (obj["content_type_id"], obj["object_id"]): cast(PermissionAssignment.PermissionType, obj["permission"])
            for obj in PermissionAssignment.objects.filter(user=self.user).values(
                "object_id", "content_type_id", "permission"
            )
        }
        permission_secondary: PermissionConfiguration = {
            (obj["content_type_id"], obj["object_id"]): cast(
                PermissionAssignment.PermissionType, obj["permission_secondary"]
            )
            for obj in PermissionAssignment.objects.filter(user=self.user).values(
                "object_id", "content_type_id", "permission_secondary"
            )
        }
        initial = [
            PermissionInitial(
                object_type=obj.object_type,
                object_id=obj.object.pk,
                object=obj.object,
                author_notes=obj.author_notes,
                round=obj.round,
                permission=self._check_current_permission(self.user, obj, permissions),
                permission_secondary=self._check_current_permission(self.user, obj, permission_secondary, binary=True),
            )
            for obj in objects
        ]
        return initial

    def get_success_url(self):
        if self.redirect:
            return reverse("wjs_article_details", kwargs={"pk": self.workflow.pk})
        return reverse("wjs_assign_permission", kwargs={"pk": self.workflow.pk, "user_id": self.user.id})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["article"] = self.workflow.article
        kwargs["user"] = self.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.add_message(self.request, messages.SUCCESS, "Permissions modified.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        context["user"] = self.user
        context["use_formset"] = True
        return context
