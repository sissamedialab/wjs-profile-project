import dataclasses
from typing import TYPE_CHECKING, Any, Dict, Optional

from core.models import AccountRole, Role
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_fsm import can_proceed
from events import logic as events_logic
from review.const import EditorialDecisions
from review.logic import assign_editor, quick_assign
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from review.views import (
    accept_review_request,
    decline_review_request,
    upload_review_file,
)
from submission.models import STAGE_ASSIGNED, STAGE_UNDER_REVISION, Article

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


@dataclasses.dataclass
class AssignToEditor:
    """
    Assigns an editor to an article and creates a review round to replicate the behaviour of janeway's move_to_review.
    """

    editor: Account
    article: Article
    request: HttpRequest
    workflow: Optional[ArticleWorkflow] = None

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

    def _log_operation(self):
        # TODO: should we use signal/events to log the operations?
        # TODO: should I record the name here also? Probably not...
        # TODO: this message does not read well in the automatic notification,
        #       but something like "{article.id} assigned..." won't read well in timeline.
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject="Assigned to editor",
            recipients=[self.editor],
        )

    def run(self) -> ArticleWorkflow:
        with transaction.atomic():
            self._create_workflow()
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self._assign_editor()
            self._update_state()
            self._log_operation()
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

    def _log_operation(self):
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject="Assigned to reviewer",
            message_body=self.form_data["message"],
            actor=self.editor,
            recipients=[self.reviewer],
            message_type=Message.MessageTypes.VERBOSE,
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
            assignment = self._assign_reviewer()
            if not assignment:
                raise ValueError(_("Cannot assign review"))
            self._log_operation()
        return assignment


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

    def _handle_accept(self) -> Optional[bool]:
        """
        Accept the review by calling janeway :py:func:`accept_review_request`.

        Response returned by janeway is discarded.

        Return boolean value of the assignment date_accepted field.
        """
        accept_review_request(request=self.request, assignment_id=self.assignment.pk)
        self.assignment.refresh_from_db()
        self._log_accept()
        if self.assignment.date_accepted:
            return True

    def _handle_decline(self) -> Optional[bool]:
        """
        Decline the review by calling janeway :py:func:`decline_review_request`.

        Response returned by janeway is discarded.

        Return boolean value of the assignment date_declined field.
        """
        decline_review_request(request=self.request, assignment_id=self.assignment.pk)
        self.assignment.refresh_from_db()
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

    def _log_accept(self):
        # TODO: exceptions here just disappear
        # try print(self.workflow.article) (no workflow in EvaluateReview instances!!!)
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject="Review assignment accepted",
            actor=self.assignment.reviewer,
            recipients=[self.assignment.editor],
        )

    def _log_decline(self):
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject="Review assignment declined",
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

    def _log_operation(self):
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject="Review submitted",
            recipients=[self.assignment.editor],
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

    @staticmethod
    def _get_email_context(
        article: Article,
        request: HttpRequest,
        form_data: Dict[str, Any],
        revision: Optional[EditorRevisionRequest] = None,
    ) -> Dict[str, Any]:
        return {
            "article": article,
            "request": request,
            "revision": revision,
            "decision": form_data["decision"],
            "user_message_content": form_data["decision_editor_report"],
            "skip": False,
        }

    def _log_accept(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject="Paper accepted",
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
            # do we have a subject? message_subject=email_context.pop("subject")
        )

    def _log_decline(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject="Paper rejected",
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
        )

    def _log_not_suitable(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject="Paper deemed not suitable",
            recipients=[self.workflow.article.correspondence_author],
            message_type=Message.MessageTypes.VERBOSE,
        )

    def _log_revision_request(self, email_context):
        # TODO: use the email_context to build a nice message
        communication_utils.log_operation(
            self.workflow.article,
            "Revision is requested",
            recipients=[self.workflow.article.correspondence_author],
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

        context = HandleDecision._get_email_context(self.workflow.article, self.request, self.form_data)
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

        context = HandleDecision._get_email_context(self.workflow.article, self.request, self.form_data)
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

        context = HandleDecision._get_email_context(self.workflow.article, self.request, self.form_data)
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
        context = HandleDecision._get_email_context(self.workflow.article, self.request, self.form_data, revision)
        self._trigger_article_event(events_logic.Events.ON_REVISIONS_REQUESTED_NOTIFY, context)
        self._log_revision_request(context)
        return revision

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
