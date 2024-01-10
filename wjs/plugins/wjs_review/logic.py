"""Business logic is here.

Most logic is encapsulated into dataclasses that take the necessary data structures upon creation and perform their
action in a method named "run()".

"""
import dataclasses
from typing import TYPE_CHECKING, Any, Dict, List, Optional

# There are many "File" classes; I'll use core_models.File in typehints for clarity.
from core import files as core_files
from core import models as core_models
from core.models import AccountRole, Role
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_fsm import can_proceed
from events import logic as events_logic
from journal.models import Journal
from review.const import EditorialDecisions
from review.logic import assign_editor, quick_assign
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from review.views import upload_review_file
from submission.models import STAGE_ASSIGNED, STAGE_UNDER_REVISION, Article
from utils.render_template import get_message_content
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

from . import communication_utils, permissions

if TYPE_CHECKING:
    from .forms import ReportForm

from .models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    Message,
    WorkflowReviewAssignment,
)

Account = get_user_model()


def render_template_from_setting(
    setting_group_name: str,
    setting_name: str,
    journal: Journal,
    request: HttpRequest,
    context: Dict[str, Any],
    template_is_setting: Optional[bool] = True,
):
    """
    Auxiliary function to "ease" the rendering of a template taken from Janeway's settings.
    """
    template = get_setting(
        setting_group_name=setting_group_name,
        setting_name=setting_name,
        journal=journal,
    ).processed_value
    rendered_template = get_message_content(
        request=request,
        context=context,
        template=template,
        template_is_setting=template_is_setting,
    )
    return rendered_template


@dataclasses.dataclass
class AssignToEditor:
    """
    Assigns an editor to an article and creates a review round to replicate the behaviour of janeway's move_to_review.
    """

    editor: Account
    article: Article
    request: HttpRequest
    workflow: Optional[ArticleWorkflow] = None
    assignment: Optional[EditorAssignment] = None

    def _create_workflow(self):
        self.workflow, __ = ArticleWorkflow.objects.get_or_create(
            article=self.article,
        )

    def _assign_editor(self) -> EditorAssignment:
        assignment, _ = assign_editor(self.article, self.editor, "section-editor", request=self.request)
        self._create_review_round()
        return assignment

    def _create_review_round(self) -> ReviewRound:
        self.article.stage = STAGE_ASSIGNED
        self.article.save()
        review_round, __ = ReviewRound.objects.get_or_create(article=self.article, round_number=1)
        return review_round

    def _update_state(self):
        """Run FSM transition."""
        self.workflow.director_selects_editor()
        self.workflow.save()

    def _check_conditions(self) -> bool:
        is_section_editor = self.editor.check_role(self.request.journal, "section-editor")
        state_conditions = can_proceed(self.workflow.director_selects_editor)
        return is_section_editor and state_conditions

    def _get_message_context(self) -> Dict[str, Any]:
        review_in_review_url = self.request.journal.site_url(
            path=reverse(
                "review_in_review",
                kwargs={"article_id": self.article.pk},
            ),
        )
        return {
            "article": self.workflow.article,
            "request": self.request,
            "editor_assigment": self.assignment,
            "editor": self.editor,
            "review_in_review_url": review_in_review_url,
        }

    def _log_operation(self, context: Dict[str, Any]):
        # TODO: should we use signal/events to log the operations?
        # TODO: should I record the name here also? Probably not...
        # TODO: this message does not read well in the automatic notification,
        #       but something like "{article.id} assigned..." won't read well in timeline.
        editor_assignment_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_editor_assignment",
            journal=self.workflow.article.journal,
            request=self.request,
            context={
                "article": self.workflow.article,
            },
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="editor_assignment",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=editor_assignment_subject,
            message_body=message_body,
            # TODO: actor=???,
            recipients=[self.editor],
            message_type=Message.MessageTypes.VERBOSE,
        )

    def run(self) -> ArticleWorkflow:
        with transaction.atomic():
            self._create_workflow()
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            # We save the assignment here because it's used by _get_message_context() to create the context
            # to be passed to _log_operation()
            self.assignment = self._assign_editor()
            self._update_state()
            context = self._get_message_context()
            self._log_operation(context=context)
        return self.workflow


@dataclasses.dataclass
class AssignToReviewer:
    """
    Assigns a reviewer by using review.logic.quick_assign and checking conditions for the assignment.

    Assigning a reviewer does not trigger a state transition.
    """

    workflow: ArticleWorkflow
    reviewer: Account
    editor: Account
    form_data: Dict[str, Any]
    request: HttpRequest
    assignment: Optional[WorkflowReviewAssignment] = None

    @staticmethod
    def check_reviewer_conditions(workflow: ArticleWorkflow, reviewer: Account) -> bool:
        """Reviewer cannot be an author of the article."""
        return reviewer not in workflow.article_authors

    @staticmethod
    def check_editor_conditions(workflow: ArticleWorkflow, editor: Account) -> bool:
        """Editor must be assigned to the article."""
        return EditorAssignment.objects.filter(article=workflow.article, editor=editor).exists()

    @staticmethod
    def check_article_conditions(workflow: ArticleWorkflow) -> bool:
        """
        Workflow state must be EDITOR_SELECTED.

        Current state must be tested explicitly because there is no FSM transition to use for checking the correct
        """
        return workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        reviewer_conditions = self.check_reviewer_conditions(self.workflow, self.reviewer)
        editor_conditions = self.check_editor_conditions(self.workflow, self.editor)
        article_state = self.check_article_conditions(self.workflow)
        return reviewer_conditions and editor_conditions and article_state

    def _ensure_reviewer(self):
        """Ensure that the reviewer has the reviewer role, assigning it if necessary."""
        has_review_role = AccountRole.objects.filter(
            user=self.reviewer,
            journal=self.workflow.article.journal,
            role__slug="reviewer",
        ).exists()
        if not has_review_role:
            AccountRole.objects.create(
                user=self.reviewer,
                journal=self.workflow.article.journal,
                role=Role.objects.get(slug="reviewer"),
            )

    def _assign_reviewer(self) -> Optional[WorkflowReviewAssignment]:
        """
        Assign the reviewer to the article.

        Use janeway review logic quick_assign function.
        """
        assignment = quick_assign(request=self.request, article=self.workflow.article, reviewer_user=self.reviewer)
        if assignment:
            if self.form_data.get("acceptance_due_date", None):
                assignment.date_due = self.form_data.get("acceptance_due_date")

            # hackish to convert a model to a subclass
            # 1. change the underlying python class
            # 2. set the id of the pointer field to the id of the original model
            # 3. save -> this creates the record in the linked table (WorkflowReviewAssignment) but keeps the original
            #    record in the ReviewAssignment table intact, so the two are now linked and we can later retrieve
            #    WorkflowReviewAssignment instance or original ReviewAssignment object and the access the linked
            #    object through the workflowreviewassignment field
            default_visibility = WorkflowReviewAssignment._meta.get_field("author_note_visible").default
            assignment_id = assignment.pk
            assignment.__class__ = WorkflowReviewAssignment
            assignment.reviewassignment_ptr_id = assignment_id
            assignment.author_note_visible = self.form_data.get("author_note_visible", default_visibility)
            assignment.save()
        return assignment

    def _get_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.workflow.article,
            "request": self.request,
            "user_message_content": self.form_data["message"],
            "skip": False,
            "review_assignment": self.assignment,
        }

    def _log_operation(self, context: Dict[str, Any]):
        if self.reviewer == self.editor:
            # TODO: review me after specs#606
            message_type = Message.MessageTypes.SYSTEM
        else:
            message_type = Message.MessageTypes.VERBOSE

        review_assignment_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_assignment",
            journal=self.workflow.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_assignment",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=review_assignment_subject,
            message_body=message_body,
            actor=self.editor,
            recipients=[self.reviewer],
            message_type=message_type,
        )

    def run(self) -> WorkflowReviewAssignment:
        # TODO: verificare in futuro se controllare assegnazione multiupla allo stesso reviewer quando si saranno
        #       decisi i meccanismi digestione dei round e delle versioni
        # TODO: se il reviewer non ha il ruolo bisogna fare l'enrolment
        # - controllare che
        #   - il reviewer possa essere assegnato
        #   - lo stato sia compatibile con "assign reviewer"
        # - assegna il reviewer
        # - invia la mail
        # - salva
        # - si emette un evento signal
        # - si ritorna l'oggetto
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            self._ensure_reviewer()
            # We save the assignment here because it's used by _get_message_context() to create the context
            # to be passed to _log_operation()
            self.assignment = self._assign_reviewer()
            if not self.assignment:
                raise ValueError(_("Cannot assign review"))
            context = self._get_message_context()
            self._log_operation(context=context)
        return self.assignment


@dataclasses.dataclass
class EvaluateReview:
    """
    Handle the decision of the reviewer to accept / decline the review and checks the conditions for the transition.
    """

    assignment: ReviewAssignment
    reviewer: Account
    editor: Account
    form_data: Dict[str, Any]
    request: HttpRequest
    token: str

    @staticmethod
    def check_reviewer_conditions(assignment: ReviewAssignment, reviewer: Account) -> bool:
        """Reviewer cannot be an author of the article."""
        return reviewer == assignment.reviewer

    @staticmethod
    def check_editor_conditions(assignment: ReviewAssignment, editor: Account) -> bool:
        """Editor must be assigned to the article."""
        return editor == assignment.editor

    @staticmethod
    def check_article_conditions(assignment: ReviewAssignment) -> bool:
        """
        Workflow state must be EDITOR_SELECTED.

        Current state must be tested explicitly because there is no FSM transition to use for checking the correct
        """
        return assignment.article.articleworkflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        reviewer_conditions = self.check_reviewer_conditions(self.assignment, self.reviewer)
        editor_conditions = self.check_editor_conditions(self.assignment, self.editor)
        date_due_set = bool(self.assignment.date_due)
        gdpr_compliant = (
            # if the reviewer is already gdpr-compliant, the gdpr field widged is not shown in the form, so the form
            # data will be empty / false. Since the gdpr check is necessary only for "invited" (new) users, it seems
            # safer to just ignore what comes from the form if the user is already compliant.
            self.reviewer.jcomprofile.gdpr_checkbox
            or self.form_data.get("accept_gdpr")
            or self.form_data.get("reviewer_decision") != "1"
        )
        article_state = self.check_article_conditions(self.assignment)
        return reviewer_conditions and editor_conditions and date_due_set and gdpr_compliant and article_state

    def _janeway_logic_handle_accept(self):
        """Accept an assignment.

        Taken from review.views.accept_review_request
        """
        self.assignment.date_accepted = timezone.now()
        self.assignment.save()

    def _handle_accept(self) -> Optional[bool]:
        """Accept the review.

        Return boolean value of the assignment date_accepted field.
        """
        self._janeway_logic_handle_accept()
        self.assignment.refresh_from_db()
        self._log_accept()
        if self.assignment.date_accepted:
            return True

    def _janeway_logic_handle_decline(self):
        """Decline an assignment.

        Taken from review.views.decline_review_request
        """
        self.assignment.date_declined = timezone.now()
        self.assignment.date_accepted = None
        self.assignment.is_complete = True
        self.assignment.save()

    def _handle_decline(self) -> Optional[bool]:
        """Decline the review.

        Return boolean value of the assignment date_declined field.
        """
        self._janeway_logic_handle_decline()
        self._log_decline()
        if self.assignment.date_declined:
            return False

    def _activate_invitation(self, token: str):
        """
        Activate user, only if accept_gdpr is set.
        """
        if self.form_data.get("accept_gdpr"):
            user = JCOMProfile.objects.get(invitation_token=token)
            user.is_active = True
            user.gdpr_checkbox = True
            user.invitation_token = ""
            user.save()
            if self.request.user == user.janeway_account:
                # request user must be refreshed to ensure flags are loaded correctly
                self.request.user.refresh_from_db()

    def _save_date_due(self):
        """
        Set and save date_due on assignment if present in form_data.
        """
        date_due = self.form_data.get("date_due")
        if date_due:
            # This can be a noop if EvaluateReview is called from EvaluateReviewForm because it's a model form
            # which already set the attribute (but the object is not saved because form save method is overridden)
            self.assignment.date_due = date_due
            self.assignment.save()

    def _get_accept_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assignment": self.assignment,
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": self.assignment.id}),
        }

    def _get_decline_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assignment": self.assignment,
        }

    def _log_accept(self):
        # TODO: exceptions here just disappear
        # try print(self.workflow.article) (no workflow in EvaluateReview instances!!!)
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_accept_acknowledgement",
            journal=self.assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_accept_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_accept_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.assignment.reviewer,
            recipients=[self.assignment.editor],
        )

    def _log_decline(self):
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_decline_acknowledgement",
            journal=self.assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_decline_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_decline_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.assignment.reviewer,
            recipients=[self.assignment.editor],
        )

    def run(self) -> Optional[bool]:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Transition conditions not met"))
            if self.token:
                self._activate_invitation(self.token)
            self._save_date_due()
            if self.form_data.get("reviewer_decision") == "1":
                return self._handle_accept()
            if self.form_data.get("reviewer_decision") == "0":
                return self._handle_decline()


@dataclasses.dataclass
class InviteReviewer:
    """
    Handle the decision of the reviewer to accept / decline the review and checks the conditions for the transition.
    """

    workflow: ArticleWorkflow
    editor: Account
    form_data: Dict[str, Any]
    request: HttpRequest

    def _generate_token(self) -> str:
        return generate_token(self.form_data["email"], self.request.journal.code)

    @staticmethod
    def check_article_conditions(workflow: ArticleWorkflow) -> bool:
        """
        Workflow state must be EDITOR_SELECTED.

        Current state must be tested explicitly because there is no FSM transition to use for checking the correct
        """
        return workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        has_journal = self.request.journal
        article_state = self.check_article_conditions(self.workflow)
        return has_journal and article_state

    def _create_user(self, token: str) -> JCOMProfile:
        user = JCOMProfile.objects.create(
            email=self.form_data["email"],
            first_name=self.form_data["first_name"],
            last_name=self.form_data["last_name"],
            is_active=False,
            invitation_token=token,
        )
        return user

    def _get_or_create_user(self, email: str) -> JCOMProfile:
        """
        The match is done via email address, and this method returns the user with the given email address,
        being it already existing or freshly created.
        The caller will be able to distinguish between "existing" or "created" user by checking invitation_token
        in the returned JCOMProfile instance.
        """
        try:
            # Try to get the user with the given email...
            user = JCOMProfile.objects.get(email=email)
        except JCOMProfile.DoesNotExist:
            # If it does not exist, generate a token and a new user with the just created token
            token = self._generate_token()
            user = self._create_user(token)
        return user

    def _notify_user(self, user: JCOMProfile):
        """Notify current user that the invitation has been sent."""
        if user.invitation_token:
            # If there is a token, the user did not exist and it was invited
            messages.add_message(self.request, messages.INFO, _("Invitation sent to %s.") % user.last_name)
        else:
            # If there is no token, the user was already existing and thus assigned to the review automatically
            messages.add_message(self.request, messages.INFO, _("%s assigned to the article review.") % user.last_name)

    def _assign_reviewer(self, user: JCOMProfile) -> WorkflowReviewAssignment:
        """Create a review assignment for the invited user."""
        assign_service = AssignToReviewer(
            reviewer=user.janeway_account,
            workflow=self.workflow,
            editor=self.editor,
            form_data=self.form_data,
            request=self.request,
        )
        return assign_service.run()

    def run(self) -> JCOMProfile:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Invitation conditions not met"))
            user = self._get_or_create_user(self.form_data["email"])
            self._assign_reviewer(user)
            # The user (which is a JCOMProfile instance) is also used to check for the invitation token and to choose
            # the right message for the notification.
            self._notify_user(user=user)
            # No need to log anything here, because the real action is AssignToReviewer.
            # TODO: or do we want to log if the reviewer has been invited (new user) or was already here?
            return user


@dataclasses.dataclass
class SubmitReview:
    assignment: ReviewAssignment
    form: "ReportForm"
    submit_final: bool
    request: HttpRequest

    @staticmethod
    def _upload_files(assignment: ReviewAssignment, request: HttpRequest) -> ReviewAssignment:
        """Upload the files for the review."""
        if request.FILES:
            assignment = upload_review_file(request, assignment_id=assignment.pk)
        return assignment

    @staticmethod
    def _save_report_form(assignment: ReviewAssignment, form: "ReportForm") -> ReviewAssignment:
        """
        Save the report form.

        Run for draft and final review.
        """
        assignment.save_review_form(form, assignment)
        assignment.refresh_from_db()
        return assignment

    @staticmethod
    def _complete_review(assignment: ReviewAssignment, submit_final: bool) -> ReviewAssignment:
        """If the user has submitted a final review, mark the assignment as complete."""
        if submit_final:
            assignment.date_complete = timezone.now()
            assignment.is_complete = True
            if not assignment.date_accepted:
                assignment.date_accepted = timezone.now()
            assignment.save()
        return assignment

    @staticmethod
    def _trigger_complete_event(assignment: ReviewAssignment, request: HttpRequest, submit_final: bool):
        """Trigger the ON_REVIEW_COMPLETE event to comply with upstream review workflow."""
        if submit_final:
            kwargs = {"review_assignment": assignment, "request": request}
            events_logic.Events.raise_event(
                events_logic.Events.ON_REVIEW_COMPLETE,
                task_object=assignment.article,
                **kwargs,
            )

    # TODO: Use this method
    # TODO: The idea is to make the context variables as flat as possible, but in this
    # stage of development the settings themselves that are already in Janeway are not
    # to be modified, as we don't know yet the content we want.
    def _get_editor_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "skip": False,
            "review_assignment": self.assignment,
        }

    # TODO: Use this method
    # TODO: The idea is to make the context variables as flat as possible, but in this
    # stage of development the settings themselves that are already in Janeway are not
    # to be modified, as we don't know yet the content we want.
    def _get_reviewer_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "skip": False,
            "review_assignment": self.assignment,
        }

    # There are two messages/mails that are sent when a reviewer completes a review:
    # - To the reviewer(s) (settings: {subject_,}review_complete_reviewer_acknowledgement)
    # - To the editor(s): (settings: {subject_,}review_complete_acknowledgement)
    def _log_operation(self):
        # Message to the reviewer
        reviewer_message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_complete_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
        ).processed_value
        reviewer_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_complete_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_reviewer_message_context(),
            template_is_setting=True,
        )
        if self.assignment.reviewer == self.assignment.editor:
            message_type = Message.MessageTypes.SYSTEM
        else:
            message_type = Message.MessageTypes.VERBOSE
        communication_utils.log_operation(
            # TODO: actor
            article=self.assignment.article,
            message_subject=reviewer_message_subject,
            message_body=reviewer_message_body,
            recipients=[self.assignment.reviewer],
            message_type=message_type,
        )
        # Message to the editor
        editor_message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_complete_acknowledgement",
            journal=self.assignment.article.journal,
        ).processed_value
        editor_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_complete_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_reviewer_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            # TODO: actor
            article=self.assignment.article,
            message_subject=editor_message_subject,
            message_body=editor_message_body,
            recipients=[self.assignment.editor],
            message_type=message_type,
        )

    def run(self):
        with transaction.atomic():
            assignment = self._upload_files(self.assignment, self.request)
            assignment = self._save_report_form(assignment, self.form)
            assignment = self._complete_review(assignment, self.submit_final)
            self._trigger_complete_event(assignment, self.request, self.submit_final)
            self._log_operation()
            return assignment


@dataclasses.dataclass
class HandleDecision:
    workflow: ArticleWorkflow
    form_data: Dict[str, Any]
    user: Account
    request: HttpRequest

    _decision_handlers = {
        ArticleWorkflow.Decisions.ACCEPT: "_accept_article",
        ArticleWorkflow.Decisions.REJECT: "_decline_article",
        ArticleWorkflow.Decisions.NOT_SUITABLE: "_not_suitable_article",
        ArticleWorkflow.Decisions.MINOR_REVISION: "_revision_article",
        ArticleWorkflow.Decisions.MAJOR_REVISION: "_revision_article",
    }

    @staticmethod
    def check_editor_conditions(workflow: ArticleWorkflow, editor: Account) -> bool:
        """Editor must be assigned to the article."""
        editor_selected = workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
        editor_has_permissions = permissions.is_article_editor(workflow, editor)
        return editor_selected and editor_has_permissions

    @staticmethod
    def check_article_conditions(workflow: ArticleWorkflow) -> bool:
        """
        Workflow state must be EDITOR_SELECTED.

        Current state must be tested explicitly because there is no FSM transition to use for checking the correct
        """
        return workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    def check_conditions(self) -> bool:
        """Check if the conditions for the decision are met."""
        editor_has_permissions = self.check_editor_conditions(self.workflow, self.user)
        article_state = self.check_article_conditions(self.workflow)
        return editor_has_permissions and article_state

    def _trigger_article_event(self, event: str, context: Dict[str, Any]):
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""

        return events_logic.Events.raise_event(event, task_object=self.workflow.article, **context)

    def _trigger_workflow_event(self):
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""
        workflow_kwargs = {
            "handshake_url": "wjs_review_list",
            "request": self.request,
            "article": self.workflow.article,
            "switch_stage": True,
        }
        self._trigger_article_event(events_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE, workflow_kwargs)

    def _get_message_context(
        self,
        revision: Optional[EditorRevisionRequest] = None,
    ) -> Dict[str, Any]:
        return {
            "article": self.workflow.article,
            "request": self.request,
            "revision": revision,
            "decision": self.form_data["decision"],
            "user_message_content": self.form_data["decision_editor_report"],
            "withdraw_notice": self.form_data["withdraw_notice"],
            "skip": False,
        }

    def _log_accept(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject="Editor accepts paper",
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
            # do we have a subject? message_subject=email_context.pop("subject")
        )

    def _log_decline(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject="Editor rejects paper",
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
        )

    def _log_not_suitable(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject="Editor deems paper not suitable",
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
        )

    def _log_revision_request(self, email_context, revision_type=None):
        # TODO: use the email_context to build a nice message
        if revision_type == EditorialDecisions.MINOR_REVISIONS:
            message_subject = "Editor requires (minor) revision"
        else:
            message_subject = "Editor requires revision"
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject=message_subject,
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
        )

    def _log_review_withdraw(self, email_context: Dict[str, str], reviewer: Account):
        review_withdraw_subject_template = get_setting(
            setting_group_name="wjs_review",
            setting_name="review_withdraw_subject",
            journal=self.workflow.article.journal,
        ).processed_value
        review_withdraw_subject = get_message_content(
            request=self.request,
            context={"article": self.workflow.article},
            template=review_withdraw_subject_template,
            template_is_setting=True,
        )
        review_withdraw_message_template = get_setting(
            setting_group_name="wjs_review",
            setting_name="review_withdraw_message",
            journal=self.workflow.article.journal,
        ).processed_value

        review_withdraw_message = get_message_content(
            request=self.request,
            context=email_context,
            template=review_withdraw_message_template,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject=review_withdraw_subject,
            recipients=[reviewer],
            message_body=review_withdraw_message,
            message_type=Message.MessageTypes.VERBOSE,
        )

    def _accept_article(self) -> Article:
        """
        Accept article.

        - Call janeway accept_article
        - Advance workflow state
        - Trigger ON_ARTICLE_ACCEPTED event
        """
        self.workflow.article.accept_article()
        # FIXME: Remove after syncing with upstream to include commit fd0464d
        self.workflow.article.snapshot_authors(self.workflow.article, force_update=False)

        self.workflow.editor_writes_editor_report()
        self.workflow.editor_accepts_paper()
        self.workflow.save()

        context = self._get_message_context()
        self._trigger_article_event(events_logic.Events.ON_ARTICLE_ACCEPTED, context)
        self._log_accept(context)
        self._trigger_workflow_event()
        return self.workflow.article

    def _decline_article(self) -> Article:
        """
        Decline article.

        The editor rejects the article, this action has nothing to do with the
        editor that does not want to work on this article anymore (editor declines
        the assignment).

        - Call janeway decline_article
        - Advance workflow state
        - Trigger ON_ARTICLE_DECLINED event
        """
        self.workflow.article.decline_article()

        self.workflow.editor_writes_editor_report()
        self.workflow.editor_rejects_paper()
        self.workflow.save()

        context = self._get_message_context()
        self._trigger_article_event(events_logic.Events.ON_ARTICLE_DECLINED, context)
        self._log_decline(context)
        return self.workflow.article

    def _not_suitable_article(self) -> Article:
        """
        Mark article as not suitable.

        - Call janeway decline_article
        - Advance workflow state
        - Trigger ON_ARTICLE_DECLINED event
        """
        self.workflow.article.decline_article()

        self.workflow.editor_writes_editor_report()
        self.workflow.editor_deems_paper_not_suitable()
        self.workflow.save()

        context = self._get_message_context()
        self._trigger_article_event(events_logic.Events.ON_ARTICLE_DECLINED, context)
        self._log_not_suitable(context)
        return self.workflow.article

    def _close_unsubmitted_reviews(self):
        """
        Mark all non completed reviews are as declined and closed.
        """
        for assignment in self.workflow.article.reviewassignment_set.filter(is_complete=False):
            # FIXME: Is this the right state?
            assignment.date_declined = timezone.now()
            assignment.is_complete = True
            assignment.save()
            # TODO: Should we email reviewers to inform them that the review is closed?

    def _revision_article(self):
        """
        Ask for article revision.

        - Update workflow and article states
        - Creare EditorRevisionRequest
        """
        self.workflow.editor_writes_editor_report()
        self.workflow.editor_requires_a_revision()
        self.workflow.save()
        self.workflow.article.stage = STAGE_UNDER_REVISION
        self.workflow.article.save()
        revision = EditorRevisionRequest.objects.create(
            article=self.workflow.article,
            editor=self.user,
            type=EditorialDecisions.MINOR_REVISIONS.value
            if self.form_data["decision"] == ArticleWorkflow.Decisions.MINOR_REVISION
            else EditorialDecisions.MAJOR_REVISIONS.value,
            date_requested=timezone.now(),
            date_due=self.form_data["date_due"],
            editor_note=self.form_data["decision_editor_report"],
            review_round=self.workflow.article.current_review_round_object(),
        )
        self._assign_files(revision)
        context = self._get_message_context(revision)
        if self.form_data["decision"] in (
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.MAJOR_REVISION,
        ):
            self._withdraw_unfinished_review_requests(email_context=context)
        self._trigger_article_event(events_logic.Events.ON_REVISIONS_REQUESTED_NOTIFY, context)
        self._log_revision_request(context, revision_type=revision.type)
        return revision

    def _assign_files(self, revision: EditorRevisionRequest):
        """Assign files to the revision request to keep track of the changes."""
        revision.manuscript_files.set(self.workflow.article.manuscript_files.all())
        revision.data_figure_files.set(self.workflow.article.data_figure_files.all())
        revision.supplementary_files.set(self.workflow.article.supplementary_files.all())
        revision.source_files.set(self.workflow.article.source_files.all())
        revision.article_history = {
            "title": self.workflow.article.title,
            "abstract": self.workflow.article.abstract,
            "keywords": list(self.workflow.article.keywords.values_list("word", flat=True)),
        }
        revision.save()

    def _withdraw_unfinished_review_requests(self, email_context: Dict[str, str]):
        """
        Mark unfinished review requests as withdrawn.
        """
        for assignment in self.workflow.article.reviewassignment_set.filter(is_complete=False):
            assignment.decision = "withdrawn"
            assignment.is_complete = True
            assignment.date_complete = timezone.now()
            assignment.save()
            self._log_review_withdraw(email_context=email_context, reviewer=assignment.reviewer)

    def _store_decision(self) -> EditorDecision:
        """Store decision information."""
        decision, __ = EditorDecision.objects.get_or_create(
            workflow=self.workflow,
            review_round=self.workflow.article.current_review_round_object(),
            defaults={
                "decision": self.form_data["decision"],
                "decision_editor_report": self.form_data["decision_editor_report"],
                "decision_internal_note": self.form_data["decision_internal_note"],
            },
        )
        return decision

    def run(self) -> EditorDecision:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Decision conditions not met"))
            decision = self._store_decision()
            handler = self._decision_handlers.get(self.form_data["decision"], None)
            if handler:
                getattr(self, handler)()
            if self.form_data["decision"]:
                self._close_unsubmitted_reviews()
            return decision


@dataclasses.dataclass
class HandleMessage:
    message: Message
    form_data: Dict[str, Any]

    def __post_init__(self):
        if ContentType.objects.get_for_model(self.message.target) == Journal:
            raise NotImplementedError("🦆")

    # TODO: refactor the following 3 methods by extending AccountManager as a manager for JCOMProfile
    @staticmethod
    def _allowed_recipients_for_actor_pks(actor: Account, article: Article) -> List[int]:
        """Return the list of ids of allowed recipients for the given actor/article combination."""
        # TODO: if a director is also an author, the system can get confused! Or, more generally, all "roles" should be
        # defined with respect to the article.

        # EO system user is always available
        # (I need to do this funny `filter(id=...)` because I need a QuerySet)
        allowed_recipients = Account.objects.filter(id=communication_utils.get_eo_user(article).id)
        others = []

        # The actor himself is always available also
        others.append(
            Account.objects.filter(id=actor.id),
        )

        articleworkflow = article.articleworkflow

        # Editor can write to:
        if permissions.is_article_editor(instance=articleworkflow, user=actor):
            # the journal's director(s)
            others.append(
                Account.objects.filter(
                    accountrole__journal=article.journal,
                    accountrole__role__slug="director",
                ),
            )
            # the correspondence author
            others.append(
                Account.objects.filter(id=article.correspondence_author.id),
            )
            # all the article's reviewers
            others.append(
                Account.objects.filter(
                    id__in=article.reviewassignment_set.all().values_list("reviewer", flat=True),
                ),
            )
        # Reviewers can write to:
        elif permissions.is_article_reviewer(instance=articleworkflow, user=actor):
            # the journal's director(s)
            others.append(
                Account.objects.filter(
                    accountrole__journal=article.journal,
                    accountrole__role__slug="director",
                ),
            )
            # "His" editor(s): only the editor that created the ReviewAssigment for this reviewer
            # I.e. not _all_ paper's editor. Other alternatives:
            # - all editors, e.g.: article.editorassignment_set.all()
            # - only the current/last editor
            others.append(
                Account.objects.filter(
                    id__in=article.reviewassignment_set.filter(reviewer=actor.id).values_list("editor", flat=True),
                ),
            )
        # Author(s) can write to:
        elif permissions.is_article_author(instance=articleworkflow, user=actor):
            # (Only) the current/last editor
            # Other alternatives:
            # - all editors, e.g. article.editorassignment_set.all()
            #
            # NB: editor assignments do not have a direct reference to a review_round (this is a wjs concept). But we
            # can use review assignments, that have a direct referenc to both editor and review_round.
            others.append(
                Account.objects.filter(
                    id__in=article.reviewassignment_set.filter(
                        review_round__round_number=article.current_review_round(),
                    ).values_list("editor", flat=True),
                ),
            )
            # the journal's director(s) (if permitted by the journal configuration)
            if get_setting(
                "wjs_review",
                "author_can_contact_director",
                article.journal,
            ).processed_value:
                others.append(
                    Account.objects.filter(
                        accountrole__journal=article.journal,
                        accountrole__role__slug="director",
                    ),
                )

        # Note that MessageForm' clean method will try to do a `get()` on this queryset, and raise
        # django.db.utils.NotSupportedError:
        #   Calling QuerySet.get(...) with filters after union() is not supported.
        # so we have to "refresh" it later on
        # and cannot "filter" it directly.
        qs = allowed_recipients.union(*others)
        return qs.values_list("id", flat=True)

    @staticmethod
    def allowed_recipients_for_actor(actor: Account, article: Article) -> QuerySet:
        """Return the list of allowed recipients for the actor of the message.

        This method is used to build the queryset for the recipient ModelChoiceField in the MessageForm, and possibly
        other places.

        """
        pks = HandleMessage._allowed_recipients_for_actor_pks(actor, article)
        return Account.objects.filter(id__in=pks)

    @staticmethod
    def can_write_to(actor: Account, article: Article, recipient: Account) -> bool:
        """Check if the sender (:py:param: actor) can write to :py:param: recipient wrt this :py:param: article."""
        pks = HandleMessage._allowed_recipients_for_actor_pks(actor, article)
        return recipient.id in pks

    def run(self):
        """Save (and send) a message."""
        recipient = get_object_or_404(Account, id=self.form_data["recipient"])
        if not Message.can_write_to(self.message.actor, self.message.target, recipient):
            raise ValidationError("Cannot write to this recipient. Please contact EO.")

        with transaction.atomic():
            self.message.message_type = Message.MessageTypes.VERBOSE
            self.message.save()
            self.message.recipients.add(recipient)
            if self.form_data["attachment"]:
                attachment: core_models.File = core_files.save_file_to_article(
                    file_to_handle=self.form_data["attachment"],
                    article=self.message.target,  # TODO: review when implementing messages for Journal
                    owner=self.message.actor,
                    label=None,  # TODO: TBD: no label (default)
                    description=None,  # TODO: TBD: no description (default)
                )
                self.message.attachments.add(attachment)
            self.message.emit_notification()
