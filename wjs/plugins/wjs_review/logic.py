"""Business logic is here.

Most logic is encapsulated into dataclasses that take the necessary data structures upon creation and perform their
action in a method named "run()".

"""

import dataclasses
import datetime
from copy import copy
from typing import Any, Dict, List, Optional

# There are many "File" classes; I'll use core_models.File in typehints for clarity.
from core import files as core_files
from core import models as core_models
from core.models import AccountRole, Role
from dateutil.parser import parse
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
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
from plugins.typesetting.models import TypesettingAssignment
from review.logic import assign_editor, quick_assign
from review.models import ReviewRound
from review.views import upload_review_file
from submission.models import STAGE_ASSIGNED, STAGE_UNDER_REVISION, Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

import wjs.jcom_profile.permissions
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.utils import generate_token, render_template_from_setting

from . import communication_utils, permissions
from .events.assignment import dispatch_assignment
from .logic__production import (  # noqa F401
    AssignTypesetter,
    AuthorSendsCorrections,
    BeginPublication,
    FinishPublication,
    ReadyForPublication,
    RequestProofs,
    UploadFile,
    VerifyProductionRequirements,
)
from .models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    Message,
    PastEditorAssignment,
    Reminder,
    WjsEditorAssignment,
    WorkflowReviewAssignment,
)
from .permissions import (
    has_any_editor_role_by_article,
    is_article_editor,
    is_article_editor_or_eo,
)
from .reminders.settings import (
    AuthorShouldSubmitMajorRevisionReminderManager,
    AuthorShouldSubmitMinorRevisionReminderManager,
    AuthorShouldSubmitTechnicalRevisionReminderManager,
    DirectorShouldAssignEditorReminderManager,
    EditorShouldMakeDecisionReminderManager,
    EditorShouldSelectReviewerReminderManager,
    ReviewerShouldEvaluateAssignmentReminderManager,
    ReviewerShouldWriteReviewReminderManager,
)
from .utils import get_other_review_assignments_for_this_round

logger = get_logger(__name__)
Account = get_user_model()

states_when_article_is_considered_archived_for_review = [
    ArticleWorkflow.ReviewStates.WITHDRAWN,
    ArticleWorkflow.ReviewStates.REJECTED,
    ArticleWorkflow.ReviewStates.NOT_SUITABLE,
]

states_when_article_is_considered_archived = [
    ArticleWorkflow.ReviewStates.WITHDRAWN,
    ArticleWorkflow.ReviewStates.REJECTED,
    ArticleWorkflow.ReviewStates.NOT_SUITABLE,
    ArticleWorkflow.ReviewStates.PUBLISHED,
]

# "In review" means articles that are
# - not archived,
# - not in states such as SUBMITTED, INCOMPLETE_SUBMISSION, PAPER_MIGHT_HAVE_ISSUES
# - not in "production" (not yet defined)
states_when_article_is_considered_in_review = [
    ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
    ArticleWorkflow.ReviewStates.PAPER_HAS_EDITOR_REPORT,
    ArticleWorkflow.ReviewStates.TO_BE_REVISED,
    ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED,
    ArticleWorkflow.ReviewStates.SUBMITTED,
    ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
]

# Editors should not see papers under appeal until the author submitted a revision,
# but EO/director should see them always
states_when_article_is_considered_in_review_for_eo_and_director = states_when_article_is_considered_in_review + [
    ArticleWorkflow.ReviewStates.UNDER_APPEAL
]

# TODO: write me!
states_when_article_is_considered_in_production = [
    ArticleWorkflow.ReviewStates.ACCEPTED,
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PROOFREADING,
    ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
]
states_when_article_is_considered_typesetter_pending = [
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
]
states_when_article_is_considered_typesetter_working_on = [
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PROOFREADING,
]
states_when_article_is_considered_production_archived = [
    ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
    ArticleWorkflow.ReviewStates.PUBLISHED,
]
states_when_article_is_considered_author_pending = [
    ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
    ArticleWorkflow.ReviewStates.UNDER_APPEAL,
]


def handle_reviewer_deassignment_reminders(assignment: WorkflowReviewAssignment):
    """Create reminders for the editor.

    When, for the current review round, this is the last reviewer from whom the Editor is expecting an action:
    if at least another review was completed (not declined) -> EditorShouldMakeDecisionReminderManager
    if no other review was completed -> EditorShouldSelectReviewerReminderManager

    """
    other_assignments = get_other_review_assignments_for_this_round(assignment)
    if not other_assignments.filter(is_complete=False).exists():
        if other_assignments.filter(is_complete=True).not_declined_or_withdrawn().exists():
            EditorShouldMakeDecisionReminderManager(
                article=assignment.article,
                editor=assignment.editor,
            ).create()
        else:
            EditorShouldSelectReviewerReminderManager(
                article=assignment.article,
                editor=assignment.editor,
            ).create()


@dataclasses.dataclass
class CreateReviewRound:
    assignment: WjsEditorAssignment
    first: bool = False

    def _get_review_round(self) -> ReviewRound:
        if self.first:
            review_round, __ = ReviewRound.objects.get_or_create(article=self.assignment.article, round_number=1)
        else:
            new_round_number = self.assignment.article.current_review_round() + 1
            review_round = ReviewRound.objects.create(article=self.assignment.article, round_number=new_round_number)
        return review_round

    def run(self) -> ReviewRound:
        with transaction.atomic():
            review_round = self._get_review_round()
            self.assignment.review_rounds.add(review_round)
            return review_round


@dataclasses.dataclass
class BaseAssignToEditor:
    """
    Assigns an editor to an article and creates a review round to replicate the behaviour of janeway's move_to_review.

    Low level service that skips checks and does not trigger a state transition: it's used by AssignToEditor and
    automatic assigment logic functions.

    request attribute **must** have user attribute set to the current user.
    """

    editor: Account
    article: Article
    request: HttpRequest
    first_assignment: bool = False

    def _assign_editor(self) -> WjsEditorAssignment:
        assignment, _ = assign_editor(self.article, self.editor, "section-editor", request=self.request)
        # This converts EditorAssignment created by assign_editor to WjsEditorAssignment, by swapping the underlying
        # class and setting the id of the pointer field to the id of the original model.
        assignment_id = assignment.pk
        assignment.__class__ = WjsEditorAssignment
        assignment.editorassignment_ptr_id = assignment_id
        assignment.save()
        current_review_round_object = self.article.current_review_round_object()
        first_review_round = self.first_assignment or not current_review_round_object
        if first_review_round:
            self._create_review_round(assignment, first_review_round=first_review_round)
        else:
            assignment.review_rounds.add(current_review_round_object)
        return assignment

    def _create_review_round(self, assignment: WjsEditorAssignment, first_review_round: bool) -> ReviewRound:
        self.article.stage = STAGE_ASSIGNED
        self.article.save()
        review_round = CreateReviewRound(assignment=assignment, first=first_review_round).run()
        return review_round

    def run(self) -> WjsEditorAssignment:
        with transaction.atomic():
            assignment = self._assign_editor()
            return assignment


@dataclasses.dataclass
class AssignToEditor:
    """
    Assigns an editor to an article and creates a review round to replicate the behaviour of janeway's move_to_review.

    request attribute **must** have user attribute set to the current user.
    """

    editor: Account
    article: Article
    request: HttpRequest
    workflow: Optional[ArticleWorkflow] = None
    assignment: Optional[WjsEditorAssignment] = None
    first_assignment: bool = False

    def _create_workflow(self):
        self.workflow, __ = ArticleWorkflow.objects.get_or_create(
            article=self.article,
        )

    def _update_state(self):
        """Run FSM transition."""
        if can_proceed(self.workflow.director_selects_editor):
            self.workflow.director_selects_editor()
        else:
            self.workflow.editor_assign_different_editor()
        self.workflow.save()

    def _check_conditions(self) -> bool:
        is_section_editor = self.editor.check_role(self.request.journal, "section-editor")
        state_condition_to_be_selected = can_proceed(self.workflow.director_selects_editor)
        state_condition_assign_different_editor = can_proceed(self.workflow.editor_assign_different_editor)
        exist_other_assignments = (
            WjsEditorAssignment.objects.get_all(self.article).exclude(editor=self.editor).count() > 1
        )
        return (
            is_section_editor
            and (state_condition_to_be_selected or state_condition_assign_different_editor)
            and not exist_other_assignments
        )

    def _get_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.workflow.article,
            "request": self.request,
            "editor_assigment": self.assignment,
            "editor": self.editor,
            # We could pass along the request, which has all journal settings linked to it,
            # instead of hitting the DB for a specific setting,
            # but we prefer to decouple logic and request
            "default_editor_assign_reviewer_days": get_setting(
                setting_group_name="wjs_review",
                setting_name="default_editor_assign_reviewer_days",
                journal=self.workflow.article.journal,
            ).processed_value,
        }

    def _log_operation(self, context: Dict[str, Any]):
        if self.request.user and self.request.user.is_authenticated and self.request.user != self.editor:
            actor = self.request.user
        else:
            actor = None
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="wjs_editor_assignment_subject",
            journal=self.workflow.article.journal,
            request=self.request,
            context={
                "article": self.workflow.article,
            },
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="wjs_editor_assignment_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=actor,
            recipients=[self.editor],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _create_editor_should_select_reviewer_reminders(self):
        """Create reminders for the editor to select a reviewer."""
        EditorShouldSelectReviewerReminderManager(self.assignment.article, self.assignment.editor).create()

    def _delete_director_reminders(self):
        """Delete director's reminder."""
        DirectorShouldAssignEditorReminderManager(
            article=self.assignment.article,
        ).delete()

    def run(self) -> WjsEditorAssignment:
        with transaction.atomic():
            self._create_workflow()
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            # We save the assignment here because it's used by _get_message_context() to create the context
            # to be passed to _log_operation(), and other places
            self.assignment = BaseAssignToEditor(
                editor=self.editor,
                article=self.article,
                request=self.request,
                first_assignment=self.first_assignment,
            ).run()
            self._update_state()
            context = self._get_message_context()
            self._log_operation(context=context)
            self._create_editor_should_select_reviewer_reminders()
            self._delete_director_reminders()
        return self.assignment


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
        return WjsEditorAssignment.objects.get_all(article=workflow).filter(editor=editor).exists()

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
        # editor attribute is more specific than global request.user, so we force the user to be the one declared
        # in the service constructor; the request copy is required to avoid polluting the global request object
        request = copy(self.request)
        request.user = self.editor
        assignment = quick_assign(request=request, article=self.workflow.article, reviewer_user=self.reviewer)
        if assignment:
            if self.form_data.get("acceptance_due_date", None):
                assignment.date_due = self.form_data.get("acceptance_due_date")
            # refs https://gitlab.sissamedialab.it/wjs/specs/-/issues/584
            if self.reviewer == self.editor:
                assignment.date_accepted = timezone.now()

            # hackish to convert a model to a subclass
            # 1. change the underlying python class
            # 2. set the id of the pointer field to the id of the original model
            # 3. save -> this creates the record in the linked table (WorkflowReviewAssignment) but keeps the original
            #    record in the ReviewAssignment table intact, so the two are now linked and we can later retrieve
            #    WorkflowReviewAssignment instance or original ReviewAssignment object and the access the linked
            #    object through the workflowreviewassignment field
            default_visibility = WorkflowReviewAssignment._meta.get_field("author_note_visible").default
            default_report_form_answers = {}
            assignment_id = assignment.pk
            assignment.__class__ = WorkflowReviewAssignment
            assignment.reviewassignment_ptr_id = assignment_id
            assignment.author_note_visible = self.form_data.get("author_note_visible", default_visibility)
            assignment.report_form_answers = self.form_data.get("report_form_answers", default_report_form_answers)
            assignment.editor_invite_message = None
            assignment.save()
            # this is needed because janeway set assignment.due_date to a datetime object, even if the field is a date
            # by refreshing it from db, the value is casted to a date object
            assignment.refresh_from_db()
        return assignment

    def _get_message_context(self) -> Dict[str, Any]:
        """
        Return a dictionary with the context for default form message.

        Provides:
        - major_revision: True if we are requesting the review for a major revision
        - minor_revision: True if we are requesting the review for a minor revision
        - already_reviewed: True if the reviewer has already been assigned to this article and completed a review
        - article: Article instance
        - journal: Journal instance
        - request: Request object
        - user_message_content: Content of the editor message
        - reviewer: Selected reviewer (it might be an unsaved model when using to render the message preview)
        - skip: False
        - review_assignment: Review assignment instance
        - acceptance_due_date: Due date for the review
        """
        try:
            review_round = self.workflow.article.reviewround_set.get(
                round_number=self.workflow.article.current_review_round() - 1,
            )
            # Consider that a reviewer has "already reviewed" this article only if
            # he completed a review (i.e. not he declined or was withdrawn)
            # See also conditions.review_done() and specs#875
            already_reviewed = (
                WorkflowReviewAssignment.objects.filter(
                    article=self.workflow.article,
                    reviewer=self.reviewer,
                    date_accepted__isnull=False,
                    is_complete=True,
                )
                .exclude(review_round=self.workflow.article.current_review_round_object())
                .exists()
            )

            revision_request = review_round.editorrevisionrequest_set.exclude(
                type=ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            ).first()
        except ReviewRound.DoesNotExist:
            revision_request = None
            already_reviewed = False
        acceptance_due_date = self.form_data.get("acceptance_due_date", self.assignment.date_due)
        if isinstance(acceptance_due_date, str):
            acceptance_due_date = parse(acceptance_due_date).date()
        # skipping tech_revision because it does not trigger a new review round
        return {
            "major_revision": revision_request and revision_request.type == ArticleWorkflow.Decisions.MAJOR_REVISION,
            "minor_revision": revision_request and revision_request.type == ArticleWorkflow.Decisions.MINOR_REVISION,
            "already_reviewed": already_reviewed,
            "article": self.workflow.article,
            "journal": self.workflow.article.journal,
            "request": self.request,
            "user_message_content": self.form_data["message"],
            "reviewer": self.form_data.get("reviewer", self.assignment.reviewer),
            "skip": False,
            "review_assignment": self.assignment,
            "acceptance_due_date": acceptance_due_date,
        }

    def _log_operation(self, context: Dict[str, Any]):
        if self.reviewer == self.editor:
            message_verbosity = Message.MessageVerbosity.TIMELINE
            message_subject_setting = "wjs_editor_i_will_review_message_subject"
            message_body_setting = "wjs_editor_i_will_review_message_body"
            message_subject_setting_group_name = message_body_setting_group_name = "wjs_review"
        else:
            message_verbosity = Message.MessageVerbosity.FULL
            message_subject_setting = "review_invitation_message_subject"
            message_body_setting = "review_invitation_message_body"
            message_subject_setting_group_name = message_body_setting_group_name = "wjs_review"

        review_assignment_subject = render_template_from_setting(
            setting_group_name=message_subject_setting_group_name,
            setting_name=message_subject_setting,
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name=message_body_setting_group_name,
            setting_name=message_body_setting,
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message = communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=review_assignment_subject,
            message_body=message_body,
            actor=self.editor,
            recipients=[self.reviewer],
            verbosity=message_verbosity,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
        self.assignment.editor_invite_message = message
        self.assignment.save()

    def _create_reviewevaluate_reminders(self) -> None:
        """Create reminders related to evaluation of this review request."""
        ReviewerShouldEvaluateAssignmentReminderManager(self.assignment).create()

    def _delete_editorselectreviewer_reminders(self):
        """Delete reminders for the editor to select a reviewer."""
        EditorShouldSelectReviewerReminderManager(self.assignment.article, self.assignment.editor).delete()

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
            self._create_reviewevaluate_reminders()
            self._delete_editorselectreviewer_reminders()
        return self.assignment


@dataclasses.dataclass
class EvaluateReview:
    """
    Handle the decision of the reviewer to accept / decline the review and checks the conditions for the transition.
    """

    assignment: WorkflowReviewAssignment
    reviewer: Account
    editor: Account
    form_data: Dict[str, Any]
    request: HttpRequest
    token: str

    @staticmethod
    def check_reviewer_conditions(assignment: WorkflowReviewAssignment, reviewer: Account) -> bool:
        """Reviewer cannot be an author of the article."""
        return reviewer == assignment.reviewer

    @staticmethod
    def check_editor_conditions(assignment: WorkflowReviewAssignment, editor: Account) -> bool:
        """Editor must be assigned to the article."""
        return editor == assignment.editor

    def check_postpone_due_date_too_far_in_the_future(self) -> bool:
        """Check if the review is postponed far in the future"""
        date_due = self.form_data.get("date_due", None)
        if date_due:
            return date_due > timezone.now().date() + datetime.timedelta(
                days=settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD,
            )
        return False

    @staticmethod
    def check_article_conditions(assignment: WorkflowReviewAssignment) -> bool:
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
            # if the reviewer is already gdpr-compliant, the gdpr field widget is not shown in the form, so the form
            # data will be empty / false. Since the gdpr check is necessary only for "invited" (new) users, it seems
            # safer to just ignore what comes from the form if the user is already compliant.
            self.reviewer.jcomprofile.gdpr_checkbox
            or self.form_data.get("accept_gdpr")
            or self.form_data.get("reviewer_decision") != "1"
        )
        article_state = self.check_article_conditions(self.assignment)
        return reviewer_conditions and editor_conditions and date_due_set and gdpr_compliant and article_state

    def _handle_postpone_too_far_in_the_future(self):
        self._log_postpone_too_far_in_the_future()

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
        self._delete_reviewevaluate_reminders()
        self._create_reviewreport_reminders()
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
        self._delete_reviewevaluate_reminders()
        handle_reviewer_deassignment_reminders(self.assignment)
        if self.assignment.date_declined:
            return False

    def _delete_reviewevaluate_reminders(self):
        """Delete reminders related to the evaluation of this review request."""
        ReviewerShouldEvaluateAssignmentReminderManager(self.assignment).delete()

    def _create_reviewreport_reminders(self):
        """Create reminders related to writing the review report."""
        ReviewerShouldWriteReviewReminderManager(self.assignment).create()

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
            if self.assignment.date_due != date_due:
                communication_utils.update_date_send_reminders(self.assignment, new_assignment_date_due=date_due)
            self.assignment.date_due = date_due
            self.assignment.save()

    def _get_postpone_too_far_in_the_future_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assignment": self.assignment,
            "reviewer": self.assignment.reviewer,
            "EO": communication_utils.get_eo_user(self.assignment.article),
            "editor": self.editor,
            "date_due": self.form_data["date_due"],
        }

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

    def _log_postpone_too_far_in_the_future(self):
        article = self.assignment.article
        journal = article.journal
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_far_future_subject",
            journal=journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_far_future_body",
            journal=journal,
            request=self.request,
            context=self._get_postpone_too_far_in_the_future_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            # No actor, system message
            article=article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.EMAIL,
            recipients=[communication_utils.get_eo_user(article)],
        )

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
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
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
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def run(self) -> Optional[bool]:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Transition conditions not met"))
            if self.token:
                self._activate_invitation(self.token)
            self._save_date_due()
            if self.check_postpone_due_date_too_far_in_the_future():
                self._handle_postpone_too_far_in_the_future()
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
            suffix=self.form_data["suffix"],
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
        form_data = copy(self.form_data)
        form_data["reviewer"] = user.janeway_account
        assign_service = AssignToReviewer(
            reviewer=user.janeway_account,
            workflow=self.workflow,
            editor=self.editor,
            form_data=form_data,
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
    assignment: WorkflowReviewAssignment
    form: forms.Form
    submit_final: bool
    request: HttpRequest

    @staticmethod
    def _upload_files(assignment: WorkflowReviewAssignment, request: HttpRequest) -> WorkflowReviewAssignment:
        """Upload the files for the review."""
        if request.FILES:
            upload_review_file(request, assignment_id=assignment.pk)
            assignment.refresh_from_db()
        return assignment

    @staticmethod
    def _save_report_form(assignment: WorkflowReviewAssignment, form: forms.Form) -> WorkflowReviewAssignment:
        """
        Save the report form.

        Run for draft and final review.
        """
        for field_name, field_value in form.cleaned_data.items():
            if isinstance(field_value, UploadedFile):
                continue
            assignment.report_form_answers[field_name] = field_value
        assignment.save()
        return assignment

    @staticmethod
    def _complete_review(assignment: WorkflowReviewAssignment, submit_final: bool) -> WorkflowReviewAssignment:
        """If the user has submitted a final review, mark the assignment as complete."""
        if submit_final:
            assignment.date_complete = timezone.now()
            assignment.is_complete = True
            if not assignment.date_accepted:
                assignment.date_accepted = timezone.now()
            assignment.save()
        return assignment

    @staticmethod
    def _trigger_complete_event(assignment: WorkflowReviewAssignment, request: HttpRequest, submit_final: bool):
        """Trigger the ON_REVIEW_COMPLETE event to comply with upstream review workflow."""
        if submit_final:
            kwargs = {"review_assignment": assignment, "request": request}
            events_logic.Events.raise_event(
                events_logic.Events.ON_REVIEW_COMPLETE,
                task_object=assignment.article,
                **kwargs,
            )

    def _get_editor_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "skip": False,
            "review_assignment": self.assignment,
        }

    def _get_reviewer_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "skip": False,
            "review_assignment": self.assignment,
        }

    def _log_operation(self):
        """
        Send messages at the end of the review process.

        There are two messages/mails that are sent when a reviewer completes a review:
        - To the reviewer(s) (settings: {subject_,}review_complete_reviewer_acknowledgement)
        - To the editor(s): (settings: {subject_,}review_complete_acknowledgement)
        """
        # Message to the reviewer
        reviewer_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_complete_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_reviewer_message_context(),
            template_is_setting=True,
        )
        reviewer_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_complete_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_reviewer_message_context(),
            template_is_setting=True,
        )
        if self.assignment.reviewer == self.assignment.editor:
            verbosity = Message.MessageVerbosity.FULL
        else:
            verbosity = Message.MessageVerbosity.EMAIL
        communication_utils.log_operation(
            # no actor as it's a system message
            article=self.assignment.article,
            message_subject=reviewer_message_subject,
            message_body=reviewer_message_body,
            recipients=[self.assignment.reviewer],
            verbosity=verbosity,
        )
        # Message to the editor
        editor_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_complete_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_reviewer_message_context(),
            template_is_setting=True,
        )
        editor_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_complete_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_reviewer_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.assignment.reviewer,
            article=self.assignment.article,
            message_subject=editor_message_subject,
            message_body=editor_message_body,
            recipients=[self.assignment.editor],
            verbosity=verbosity,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _delete_reviewreport_reminders(self):
        """Delete reminders related to the submission of the review report.

        It is possible that the reviewer submits a review even if he never explicitly accepted the assignement.

        In this case,
        - no REVIEWER_SHOULD_WRITE_REVIEW reminders have been created
        - here still exist the REVIEWER_SHOULD_EVALUATE_ASSIGNMENT reminders

        So we need to find and delete the right ones. This is easy, because we can just delete all reminders related to
        this assignment.

        """
        target = self.assignment
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.id,
        ).delete()

    def _create_editor_should_make_decision_reminders_maybe(self):
        """Create reminders for the editor to make a decision.

        Only when, for the current review round, there is no other pending assignment.

        The editor could also select another reviewer, but since the most important action is to make a decision, we
        should create reminders for that action.

        """
        other_assignments = get_other_review_assignments_for_this_round(self.assignment)
        if not other_assignments.filter(is_complete=False).exists():
            # ≊ article.active_reviews.
            # NB: don't use Janeway's article.active_reviews since it includes "withdrawn" reviews.
            EditorShouldMakeDecisionReminderManager(
                article=self.assignment.article,
                editor=self.assignment.editor,
            ).create()

    def run(self):
        with transaction.atomic():
            assignment = self._upload_files(self.assignment, self.request)
            assignment = self._save_report_form(assignment, self.form)
            assignment = self._complete_review(assignment, self.submit_final)
            self._trigger_complete_event(assignment, self.request, self.submit_final)
            self._log_operation()
            self._delete_reviewreport_reminders()
            self._create_editor_should_make_decision_reminders_maybe()
            return assignment


@dataclasses.dataclass
class AuthorHandleRevision:
    revision: EditorRevisionRequest
    form_data: Dict[str, Any]
    user: Account
    request: HttpRequest

    def _confirm_revision(self):
        self.revision.date_completed = timezone.now()
        self.revision.save()

    @staticmethod
    def _trigger_complete_event(revision: EditorRevisionRequest, request: HttpRequest):
        """Trigger the ON_REVIEW_COMPLETE event to comply with upstream review workflow."""
        kwargs = {
            "revision": revision,
            "request": request,
        }
        events_logic.Events.raise_event(events_logic.Events.ON_REVISIONS_COMPLETE, **kwargs)

    def _get_revision_submission_message_context(self) -> Dict[str, Any]:
        self.appeal_editor = WjsEditorAssignment.objects.get_current(article=self.revision.article).editor
        return {
            "article": self.revision.article,
            "request": self.request,
            "skip": False,
            "revision": self.revision,
            "appeal_editor": self.appeal_editor,
        }

    def _was_under_appeal(self) -> bool:
        """Returns True if the paper was under appeal"""
        return self.revision.type == ArticleWorkflow.Decisions.OPEN_APPEAL

    def _notify_reviewers(self):
        """
        Send notifications to all reviewers of unsubmitted revisions.

        Unsubmitted reviews are available only in case of technical revisions, because for major / minor revisions
        reviewers are withdrawn when requesting the revision.
        """
        reviewer_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="revision_submission_subject",
            journal=self.revision.article.journal,
        ).processed_value
        reviewer_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="revision_submission_body",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )

        for assignment in self.revision.article.active_revision_requests():
            communication_utils.log_operation(
                actor=self.revision.editor,
                article=self.revision.article,
                message_subject=reviewer_message_subject,
                message_body=reviewer_message_body,
                recipients=[assignment.reviewer],
                verbosity=Message.MessageVerbosity.FULL,
                hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
                notify_actor=communication_utils.should_notify_actor(),
                flag_as_read=False,
                flag_as_read_by_eo=True,
            )

    def _notify_editor(self):
        """Send notification to the editor."""
        reviewer_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="revision_submission_subject",
            journal=self.revision.article.journal,
        ).processed_value
        reviewer_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="revision_submission_body",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.revision.article,
            message_subject=reviewer_message_subject,
            message_body=reviewer_message_body,
            recipients=[self.revision.editor],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )

    def _notify_editor_with_appeal(self):
        """Send notification to the editor informing that the paper was under appeal."""
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="author_submits_appeal_subject",
            journal=self.revision.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="author_submits_appeal_body",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.revision.article,
            message_subject=message_subject,
            message_body=message_body,
            recipients=[self.appeal_editor],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _log_operation(self):
        """Send notifications to editor and reviewers."""
        if self._was_under_appeal():
            self._notify_editor_with_appeal()
        else:
            self._notify_editor()
        self._notify_reviewers()

    def _save_author_note(self):
        self.revision.author_note = self.form_data.get("author_note", "")
        self.revision.save()

    def run(self):
        with transaction.atomic():
            self._confirm_revision()
            self._save_author_note()
            self._trigger_complete_event(self.revision, self.request)
            self._log_operation()
            return self.revision


@dataclasses.dataclass
class WithdrawReviewRequests:
    """
    Mark review requests as withdrawn and log a personalized message.
    """

    article: Article
    request: HttpRequest
    subject_name: str
    body_name: str
    context: Dict[str, Any]
    user: Account = None

    def run(self):
        for assignment in self.article.reviewassignment_set.filter(is_complete=False):
            assignment.withdraw()

            self._log_review_withdraw(reviewer=assignment.reviewer)

    def _log_review_withdraw(self, reviewer: Account):
        self.context["recipient"] = reviewer
        review_withdraw_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name=self.subject_name,
            journal=self.article.journal,
            request=self.request,
            context=self.context,
            template_is_setting=True,
        )
        review_withdraw_message = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name=self.body_name,
            journal=self.article.journal,
            request=self.request,
            context=self.context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.article,
            message_subject=review_withdraw_subject,
            recipients=[reviewer],
            message_body=review_withdraw_message,
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )


@dataclasses.dataclass
class HandleDecision:
    workflow: ArticleWorkflow
    form_data: Dict[str, Any]
    user: Account
    request: HttpRequest
    admin_form: bool = False
    """
    admin_form is a flag to indicate that the form is being used in admin mode, where the user is an admin and can
    bypass some of the checks that are normally done for regular users and use different transitions
    """

    _decision_handlers = {
        ArticleWorkflow.Decisions.ACCEPT: "_accept_article",
        ArticleWorkflow.Decisions.REJECT: "_decline_article",
        ArticleWorkflow.Decisions.NOT_SUITABLE: "_not_suitable_article",
        ArticleWorkflow.Decisions.REQUIRES_RESUBMISSION: "_requires_resubmission",
        ArticleWorkflow.Decisions.MINOR_REVISION: "_revision_article",
        ArticleWorkflow.Decisions.MAJOR_REVISION: "_revision_article",
        ArticleWorkflow.Decisions.OPEN_APPEAL: "_revision_article",
        ArticleWorkflow.Decisions.TECHNICAL_REVISION: "_technical_revision_article",
    }

    @staticmethod
    def check_editor_conditions(workflow: ArticleWorkflow, editor: Account, admin_mode: bool) -> bool:
        """Editor must be assigned to the article."""
        if admin_mode:
            return has_eo_role(editor)
        else:
            editor_has_permissions = permissions.is_article_editor(workflow, editor)
            return editor_has_permissions

    @staticmethod
    def check_article_conditions(workflow: ArticleWorkflow, admin_mode: bool) -> bool:
        """
        Workflow state must be in a state that allows the decision to be made.

        Current state must be tested explicitly because there is no FSM transition to use for checking the correct
        initial state.

        Checked states are different between admin and non-admin mode.
        """
        if admin_mode:
            return workflow.state in (
                ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
                ArticleWorkflow.ReviewStates.REJECTED,
            )
        else:
            return workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED

    def check_conditions(self) -> bool:
        """Check if the conditions for the decision are met."""
        editor_has_permissions = self.check_editor_conditions(self.workflow, self.user, self.admin_form)
        article_state = self.check_article_conditions(self.workflow, self.admin_form)
        handler_exists = self.form_data["decision"] in self._decision_handlers
        return editor_has_permissions and article_state and handler_exists

    def _trigger_article_event(self, event: str, context: Dict[str, Any]):
        """Trigger the given event."""
        return events_logic.Events.raise_event(event, task_object=self.workflow.article, **context)

    def _get_message_context(
        self,
        revision: Optional[EditorRevisionRequest] = None,
    ) -> Dict[str, Any]:
        context = {
            "article": self.workflow.article,
            "request": self.request,
            "revision": revision,
            "decision": self.form_data["decision"],
            "user_message_content": self.form_data["decision_editor_report"],
            "withdraw_notice": self.form_data.get("withdraw_notice", ""),
            "skip": False,
        }
        if revision:
            context.update(
                {
                    "major_revision": revision.type == ArticleWorkflow.Decisions.MAJOR_REVISION,
                    "minor_revision": revision.type == ArticleWorkflow.Decisions.MINOR_REVISION,
                    "tech_revision": revision.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION,
                },
            )
        else:
            context.update(
                {
                    "major_revision": False,
                    "minor_revision": False,
                    "tech_revision": False,
                },
            )
        return context

    def _log_accept(self, context: Dict[str, Any]):
        accept_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_decision_accept",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        accept_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_decision_accept",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=accept_message_subject,
            message_body=accept_message_body,
            actor=self.user,
            recipients=[self.workflow.article.correspondence_author],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _log_decline(self, context):
        decline_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_decision_decline",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        decline_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_decision_decline",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=decline_message_subject,
            message_body=decline_message_body,
            actor=self.user,
            recipients=[self.workflow.article.correspondence_author],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _log_not_suitable(self, context):
        not_suitable_message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_not_suitable_subject",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        not_suitable_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_not_suitable_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=not_suitable_message_subject,
            message_body=not_suitable_message_body,
            actor=self.user,
            recipients=[self.workflow.article.correspondence_author],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _log_requires_resubmission(self, context):
        requires_resubmission_message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_requires_resubmission_subject",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        requires_resubmission_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_requires_resubmission_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=requires_resubmission_message_subject,
            message_body=requires_resubmission_message_body,
            actor=self.user,
            recipients=[self.workflow.article.correspondence_author],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _log_revision_request(self, context, revision_type=None):
        revision_request_message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_revision_request_subject",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        revision_request_message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="review_decision_revision_request_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject=revision_request_message_subject,
            recipients=[self.workflow.article.correspondence_author],
            message_body=revision_request_message_body,
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read_by_eo=True,
        )

    def _log_technical_revision_request(self, context: Dict[str, str]):
        technical_revision_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="technical_revision_subject",
            journal=self.workflow.article.journal,
            request=self.request,
            context={"article": self.workflow.article},
            template_is_setting=True,
        )
        technical_revision_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="technical_revision_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject=technical_revision_subject,
            recipients=[self.workflow.article.correspondence_author],
            message_body=technical_revision_body,
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=False,
            flag_as_read_by_eo=True,
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
        self._withdraw_unfinished_review_requests(email_context=context)
        self._log_accept(context)
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
        self._withdraw_unfinished_review_requests(email_context=context)
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

        if self.admin_form:
            self.workflow.admin_deems_paper_not_suitable()
        else:
            self.workflow.editor_writes_editor_report()
            self.workflow.editor_deems_paper_not_suitable()
        self.workflow.save()

        context = self._get_message_context()
        self._trigger_article_event(events_logic.Events.ON_ARTICLE_DECLINED, context)
        self._withdraw_unfinished_review_requests(email_context=context)
        self._log_not_suitable(context)
        return self.workflow.article

    def _requires_resubmission(self) -> Article:
        """
        Mark article as requires resubmission.

        - Change workflow state
        - Trigger ON_ARTICLE_DECLINED event
        """
        self.workflow.admin_or_system_requires_revision()
        self.workflow.save()

        context = self._get_message_context()
        self._withdraw_unfinished_review_requests(email_context=context)
        self._log_requires_resubmission(context)
        return self.workflow.article

    def _technical_revision_article(self):
        """
        Ask for article technical revision.

        - Create EditorRevisionRequest
        - Store historical article metadata / files
        - Send notification to author
        """
        self.workflow.editor_writes_editor_report()
        self.workflow.editor_requires_a_revision()
        self.workflow.save()
        revision = EditorRevisionRequest.objects.create(
            article=self.workflow.article,
            editor=self.user,
            type=ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            date_requested=timezone.now(),
            date_due=self.form_data["date_due"],
            editor_note=self.form_data["decision_editor_report"],
            review_round=self.workflow.article.current_review_round_object(),
        )
        self._assign_files(revision)
        context = self._get_message_context(revision)
        self._log_technical_revision_request(context)
        AuthorShouldSubmitTechnicalRevisionReminderManager(
            revision_request=revision,
        ).create()
        return revision

    def _revision_article(self):
        """
        Ask for article revision.

        - Update workflow and article states
        - Creare EditorRevisionRequest
        """
        if self.form_data["decision"] in [
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.MAJOR_REVISION,
        ]:
            self.workflow.editor_writes_editor_report()
            self.workflow.editor_requires_a_revision()
        elif self.form_data["decision"] == ArticleWorkflow.Decisions.OPEN_APPEAL:
            self.workflow.admin_opens_an_appeal()
        self.workflow.save()
        self.workflow.article.stage = STAGE_UNDER_REVISION
        self.workflow.article.save()
        revision = EditorRevisionRequest.objects.create(
            article=self.workflow.article,
            editor=self.user,
            type=self.form_data["decision"],
            date_requested=timezone.now(),
            date_due=self.form_data["date_due"],
            editor_note=self.form_data["decision_editor_report"],
            review_round=self.workflow.article.current_review_round_object(),
        )
        self._assign_files(revision)
        context = self._get_message_context(revision)
        self._withdraw_unfinished_review_requests(email_context=context)
        self._trigger_article_event(events_logic.Events.ON_REVISIONS_REQUESTED_NOTIFY, context)
        if self.form_data["decision"] in [
            ArticleWorkflow.Decisions.MINOR_REVISION,
            ArticleWorkflow.Decisions.MAJOR_REVISION,
        ]:
            # For the decision OPEN_APPEAL, the logging of the operation has already been taken care of by the
            # OpenAppeal logic.
            self._log_revision_request(context=context, revision_type=revision.type)
        revision.refresh_from_db()
        if self.form_data["decision"] == ArticleWorkflow.Decisions.MINOR_REVISION:
            AuthorShouldSubmitMinorRevisionReminderManager(
                revision_request=revision,
            ).create()
        elif self.form_data["decision"] == ArticleWorkflow.Decisions.MAJOR_REVISION:
            AuthorShouldSubmitMajorRevisionReminderManager(
                revision_request=revision,
            ).create()
        return revision

    def _assign_files(self, revision: EditorRevisionRequest):
        """Assign files to the revision request to keep track of the changes."""
        revision.manuscript_files.set(self.workflow.article.manuscript_files.all())
        revision.data_figure_files.set(self.workflow.article.data_figure_files.all())
        revision.supplementary_files.set(self.workflow.article.supplementary_files.all())
        revision.source_files.set(self.workflow.article.source_files.all())

        # We store the old Keywords' "word" instead of their ids. Doing so allows us to maintain a memory of the
        # original kwds even if they have been modified or deleted.
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
        service = WithdrawReviewRequests(
            article=self.workflow.article,
            request=self.request,
            subject_name="review_withdraw_subject",
            body_name="review_withdraw_body",
            context=email_context,
            user=self.user,
        )
        service.run()

    def _store_decision(self) -> EditorDecision:
        """Store decision information."""
        decision = EditorDecision.objects.create(
            workflow=self.workflow,
            review_round=self.workflow.article.current_review_round_object(),
            decision=self.form_data["decision"],
            decision_editor_report=self.form_data["decision_editor_report"],
        )
        return decision

    def _delete_editor_reminders(self):
        """Delete all reminders for the editor.

        When the editor makes a decision, he is done.
        """
        # When in admin mode, there probably is no WjsEditorAssignment.
        editor_assignment: WjsEditorAssignment = (
            WjsEditorAssignment.objects.get_all(article=self.workflow)
            .filter(
                editor=self.user,
            )
            .first()
        )
        if editor_assignment:
            Reminder.objects.filter(
                content_type=ContentType.objects.get_for_model(editor_assignment),
                object_id=editor_assignment.id,
            ).delete()

    def run(self) -> EditorDecision:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Decision conditions not met"))
            decision = self._store_decision()
            handler = self._decision_handlers.get(self.form_data["decision"], None)
            if handler:
                getattr(self, handler)()
                self._delete_editor_reminders()
            return decision


@dataclasses.dataclass
class PostponeRevisionRequestDueDate:
    """
    Business logic to postpone the value of EditorRevisionRequest.date_due.
    """

    revision_request: EditorRevisionRequest
    form_data: Dict[str, Any]
    request: HttpRequest

    def _check_postponed_date_due_too_far_future(self) -> bool:
        max_threshold = settings.REVISION_REQUEST_DATE_DUE_MAX_THRESHOLD
        max_date = timezone.localtime(timezone.now()).date() + datetime.timedelta(days=max_threshold)
        return self.form_data["date_due"] >= max_date

    def _get_message_context(self) -> Dict[str, Any]:
        assignment = self.revision_request.review_round.reviewassignment_set.last()
        return {
            "article": self.revision_request.article,
            "request": self.request,
            "reviewer": assignment.reviewer,
            "EO": communication_utils.get_eo_user(self.revision_request.article),
            "editor": self.revision_request.editor,
            "date_due": self.form_data["date_due"],
        }

    def _log_eo_date_due_too_far_future(self):
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="revision_request_date_due_far_future_subject",
            journal=self.revision_request.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="revision_request_date_due_far_future_body",
            journal=self.revision_request.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.revision_request.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.EMAIL,
            recipients=[communication_utils.get_eo_user(self.revision_request.article)],
        )

    def _log_author_if_date_due_is_postponed(self):
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="revision_request_date_due_postponed_subject",
            journal=self.revision_request.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="revision_request_date_due_postponed_body",
            journal=self.revision_request.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.revision_request.editor,
            article=self.revision_request.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.FULL,
            recipients=[self.revision_request.article.correspondence_author],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _save_date_due(self):
        self.revision_request.date_due = self.form_data["date_due"]
        self.revision_request.save()

    # TODO: Really check the conditions
    def check_conditions(self):
        """Check if the conditions for the assignment are met."""
        return True

    def run(self):
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Decision conditions not met"))
            self._save_date_due()
            if self._check_postponed_date_due_too_far_future():
                self._log_eo_date_due_too_far_future()
            self._log_author_if_date_due_is_postponed()


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
            # the Corresponding author
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
            # - all editors, e.g.: article.articleworkflow.get_editor_assignments()
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
            # - all editors, e.g. article.articleworkflow.get_editor_assignments()
            #
            # NB: editor assignments do not have a direct reference to a review_round (this is a wjs concept). But we
            # can use review assignments, that have a direct reference to both editor and review_round.
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
            self.message.message_type = Message.MessageTypes.USER
            self.message.verbosity = Message.MessageVerbosity.FULL
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


@dataclasses.dataclass
class AdminActions:
    """
    Service to handle "special" actions on the article workflow.

    This service is meant to handle the transitions which are not part of the normal workflow.
    """

    user: Account
    workflow: ArticleWorkflow
    decision: str
    request: HttpRequest

    _decision_handlers = {
        "dispatch": "_queue_for_assignment",
    }

    def _check_article_state_condition(self, workflow: ArticleWorkflow) -> bool:
        """Check if the article is in PAPER_MIGHT_HAVE_ISSUES state."""
        return workflow.state == ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES

    def _check_user_condition(self, user: Account) -> bool:
        """Check if the user is an EO."""
        return has_eo_role(user)

    def check_conditions(self) -> bool:
        """Check if the conditions for the decision are met."""
        article_state = self._check_article_state_condition(self.workflow)
        user = self._check_user_condition(self.user)
        return article_state and user

    def _get_message_context(
        self,
        workflow: Article,
    ) -> Dict[str, Any]:
        context = {
            "article": workflow.article,
            "request": self.request,
        }
        return context

    def _log_reassign(self, context: Dict[str, str]):
        requeue_article_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="requeue_article_subject",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        requeue_article_message = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="requeue_article_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.user,
            article=self.workflow.article,
            message_subject=requeue_article_subject,
            message_body=requeue_article_message,
            verbosity=Message.MessageVerbosity.TIMELINE,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _queue_for_assignment(self) -> ArticleWorkflow:
        """
        Queue the article for assignment.

        Set the state to EDITOR_SELECTED and dispatch the assignment.
        """
        self.workflow.admin_deems_issues_not_important()
        self.workflow.save()
        dispatch_assignment(article=self.workflow.article, request=self.request)
        self.workflow.refresh_from_db()
        self._log_reassign(self._get_message_context(workflow=self.workflow))
        return self.workflow

    def run(self) -> Article:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Decision conditions not met"))
            handler = self._decision_handlers.get(self.decision, None)
            if handler:
                workflow = getattr(self, handler)()
            return workflow


@dataclasses.dataclass
class PostponeReviewerDueDate:
    """
    Handle the decision of the editor to postpone the due date of the reviewer acceptance / report.
    """

    assignment: WorkflowReviewAssignment
    editor: Account
    form_data: Dict[str, Any]
    request: HttpRequest

    def _report_postponed_far_future_date(self) -> bool:
        """Check if the editor postponed due date far in the future."""
        if self.form_data["date_due"] > timezone.localtime(timezone.now()).date() + datetime.timedelta(
            days=settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD,
        ):
            return True

    def _get_message_context(self) -> Dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assigment": self.assignment,
            "reviewer": self.assignment.reviewer,
            "EO": communication_utils.get_eo_user(self.assignment.article),
            "editor": self.editor,
            "date_due": self.form_data["date_due"],
        }

    def _log_reviewer_if_date_is_postponed(self) -> None:
        """Log a warning for the reviewer if the due date is postponed."""
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_postpone_subject",
            journal=self.assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_postpone_body",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.FULL,
            actor=self.assignment.editor,
            recipients=[self.assignment.reviewer],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _log_eo_far_future_date(self) -> None:
        """Log a warning for the EO if the editor postponed due date far in the future."""
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_far_future_subject",
            journal=self.assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_far_future_body",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            # no actor as it's a system message
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.EMAIL,
            recipients=[communication_utils.get_eo_user(self.assignment.article)],
        )

    def _save_reviewer_date_due(self):
        """
        Set and save the postponed date_due.
        """
        self.assignment.date_due = self.form_data.get("date_due")
        self.assignment.save()

    @staticmethod
    def check_editor_conditions(assignment: WorkflowReviewAssignment, editor: Account) -> bool:
        """Editor must be assigned to the article."""
        return editor == assignment.editor

    def check_date_conditions(self) -> bool:
        """Check if the date is in the future."""
        return self.form_data["date_due"] > timezone.localtime(timezone.now()).date()

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        editor_conditions = self.check_editor_conditions(self.assignment, self.editor)
        date_conditions = self.check_date_conditions()
        return editor_conditions and date_conditions

    def run(self):
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Conditions not met"))
            self._save_reviewer_date_due()
            if self._report_postponed_far_future_date():
                self._log_eo_far_future_date()
            self._log_reviewer_if_date_is_postponed()


@dataclasses.dataclass
class BaseDeassignEditor:
    """Base Editor deassignment logic. An editor is detached from an article."""

    assignment: WjsEditorAssignment
    editor: Account
    request: HttpRequest

    @staticmethod
    def _check_editor_conditions(assignment: WjsEditorAssignment, editor: Account) -> bool:
        """Editor must be assigned to the article."""
        return editor == assignment.editor

    def check_conditions(self):
        """Check if the conditions for the assignment are met."""
        editor_conditions = self._check_editor_conditions(self.assignment, self.editor)
        return editor_conditions

    def _delete_assignment(self) -> PastEditorAssignment:
        """
        Delete the assignment and backup data to custom model.

        All existing review rounds are link to PastEditorAssignment as the editor keeps visibility of the review
        rounds.
        """
        self._delete_editor_reminders()
        past = PastEditorAssignment.objects.create(
            editor=self.assignment.editor,
            article=self.assignment.article,
            date_assigned=self.assignment.assigned,
            date_unassigned=timezone.now(),
        )
        migrated_review_rounds = self.assignment.review_rounds.all()

        past.review_rounds.add(*migrated_review_rounds)
        self.assignment.delete()
        return past

    def _delete_editor_reminders(self):
        """Delete all reminders for the editor."""
        EditorShouldMakeDecisionReminderManager(self.assignment.article, self.assignment.editor).delete()
        EditorShouldSelectReviewerReminderManager(self.assignment.article, self.assignment.editor).delete()

    def run(self):
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            return self._delete_assignment()


@dataclasses.dataclass
class HandleEditorDeclinesAssignment:
    """
    Handle disassociation of an editor from an article followed by a declination of editor assignment.
    """

    assignment: WjsEditorAssignment
    editor: Account
    request: HttpRequest
    director: Optional[Account] = None

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "editor": self.editor,
            "director": self.director,
            "article": self.assignment.article,
        }

    def _log_director(self):
        """Logs a message to the Director containing information about the motivation of the declination."""
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="editor_decline_assignment_subject",
            journal=self.assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="editor_decline_assignment_default",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.FULL,
            actor=self.editor,
            recipients=[self.director],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )

    def _update_state(self):
        self.assignment.article.articleworkflow.ed_declines_assignment()
        self.assignment.article.articleworkflow.save()

    def _create_director_reminder(self):
        """Create a reminder for the director."""
        DirectorShouldAssignEditorReminderManager(
            article=self.assignment.article,
        ).create()

    def run(self) -> PastEditorAssignment:
        with transaction.atomic():
            try:
                past_assignment = BaseDeassignEditor(self.assignment, self.editor, self.request).run()
            except ValueError:
                raise
            self._create_director_reminder()
            self._update_state()
            self.director = communication_utils.get_director_user(self.assignment.article)
            if self.request.user == self.editor and self.director:
                self._log_director()
            return past_assignment


@dataclasses.dataclass
class DeselectReviewer:
    """
    Remove reviewer assignment
    """

    assignment: WorkflowReviewAssignment
    editor: Account
    request: HttpRequest
    send_reviewer_notification: bool
    form_data: Dict[str, Any]

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "editor": self.editor,
            "assignment": self.assignment,
        }

    def _log_reviewer(self):
        """Logs a message to the reviewer containing information about the motivation of the deassignment."""
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=self.form_data.get("notification_subject"),
            message_body=self.form_data.get("notification_body"),
            verbosity=Message.MessageVerbosity.FULL,
            actor=self.editor,
            recipients=[self.assignment.reviewer],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _log_system(self):
        """Logs a system message notifying for deassignment."""
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="editor_deassign_reviewer_system_subject",
            journal=self.assignment.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="editor_deassign_reviewer_system_body",
            journal=self.assignment.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.editor,
            verbosity=Message.MessageVerbosity.EMAIL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    @staticmethod
    def _check_editor_conditions(assignment: WorkflowReviewAssignment, editor: Account) -> bool:
        """Current editor must be article editor."""
        return is_article_editor_or_eo(assignment.article.articleworkflow, editor)

    def check_conditions(self):
        """Check if the conditions for the deassignment are met."""
        editor_conditions = self._check_editor_conditions(self.assignment, self.editor)
        return editor_conditions

    def _withdraw_assignment(self) -> bool:
        """
        Withdraw the assignment
        """
        self._delete_reviewer_reminders()
        handle_reviewer_deassignment_reminders(self.assignment)
        if self.send_reviewer_notification:
            self._log_reviewer()
        else:
            self._log_system()
        self.assignment.withdraw()
        return True

    def _delete_reviewer_reminders(self):
        """Delete all reminders for the deassigned reviewer."""
        ReviewerShouldEvaluateAssignmentReminderManager(self.assignment).delete()
        ReviewerShouldWriteReviewReminderManager(self.assignment).delete()

    def run(self) -> bool:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            success = self._withdraw_assignment()
            return success


@dataclasses.dataclass
class OpenAppeal:
    new_editor: Account
    article: Article
    request: HttpRequest

    @staticmethod
    def _is_current_editor(article: Article, editor: Account) -> bool:
        """Current editor must be article editor."""
        return is_article_editor(article.articleworkflow, editor)

    def _is_articles_author(self) -> bool:
        """Check if selected Editor is the article's author."""
        return self.article.authors.filter(id=self.new_editor.id).exists()

    def _has_another_past_rejection(self) -> bool:
        return (
            EditorDecision.objects.filter(
                workflow=self.article.articleworkflow,
                decision=ArticleWorkflow.Decisions.REJECT,
            ).count()
            > 1
        )

    def check_conditions(self):
        """Check if the selected editor is an actual editor for the article's journal."""
        editor_conditions = has_any_editor_role_by_article(self.article.articleworkflow, self.new_editor)
        return editor_conditions and not self._is_articles_author() and not self._has_another_past_rejection()

    def _handle_decision(self):
        """Instantiate HandleDecision to create the EditorRevisionRequest and the other collateral effects."""
        form_data = {
            "decision": ArticleWorkflow.Decisions.OPEN_APPEAL,
            "decision_editor_report": "",
            "acceptance_due_date": None,
            "date_due": timezone.now() + datetime.timedelta(days=settings.REVISION_REQUEST_DATE_DUE_MAX_THRESHOLD),
        }
        HandleDecision(
            workflow=self.article.articleworkflow,
            form_data=form_data,
            user=self.request.user,
            request=self.request,
            admin_form=True,
        ).run()

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "article": self.article,
        }

    def _log_author(self):
        """Logs a message to the Author informing about the appeal."""
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="eo_opens_appeal_subject",
            journal=self.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="eo_opens_appeal_body",
            journal=self.article.journal,
            request=self.request,
            context=self._get_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.new_editor,
            recipients=[self.article.correspondence_author],
        )

    def deassign_current_editor(self):
        """Deassigns the current editor using our existing logic."""
        current_assignment = WjsEditorAssignment.objects.get_current(article=self.article)
        BaseDeassignEditor(assignment=current_assignment, editor=current_assignment.editor, request=self.request).run()

    def assign_new_editor(self):
        """Assigns newly selected editor using our existing logic."""
        BaseAssignToEditor(editor=self.new_editor, article=self.article, request=self.request).run()

    def run(self):
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            if not self._is_current_editor(self.article, self.new_editor):
                self.deassign_current_editor()
                self.assign_new_editor()
            self._handle_decision()
            self._log_author()


@dataclasses.dataclass
class WithdrawPreprint:
    """Withdraw preprint."""

    workflow: ArticleWorkflow
    request: HttpRequest
    form_data: Dict[str, Any]

    def _check_user_conditions(self) -> bool:
        """Check if the user is the correspondence author."""
        return self.workflow.article.correspondence_author == self.request.user

    def _has_past_rejection(self) -> bool:
        """Check if the article was already rejected one time."""
        return EditorDecision.objects.filter(
            workflow=self.workflow,
            decision=ArticleWorkflow.Decisions.REJECT,
        ).exists()

    def _check_state_conditions(self) -> bool:
        """Check if the FSM transition can be made."""
        withdraw_without_rejection = (
            can_proceed(self.workflow.author_withdraws_preprint) and not self._has_past_rejection()
        )
        withdraw_after_a_rejection = (
            can_proceed(self.workflow.author_withdraws_preprint_after_a_rejection) and self._has_past_rejection()
        )
        return withdraw_without_rejection or withdraw_after_a_rejection

    def _check_conditions(self) -> bool:
        """Check if the conditions for the withdrawal are met."""
        return self._check_user_conditions() and self._check_state_conditions()

    def _close_review_assignments(self):
        """Close all the review assignments and log reviewers."""
        service = WithdrawReviewRequests(
            article=self.workflow.article,
            request=self.request,
            subject_name="preprint_withdrawn_subject",
            body_name="preprint_withdrawn_body",
            context={"article": self.workflow.article},
        )
        service.run()

    def _update_state(self):
        """Run FSM transition."""
        if self._has_past_rejection() and can_proceed(self.workflow.author_withdraws_preprint_after_a_rejection):
            self.workflow.author_withdraws_preprint_after_a_rejection()
        else:
            self.workflow.author_withdraws_preprint()
        self.workflow.save()

    def _log_supervisor(self):
        """Logs a message to editor or EO containing information about the motivation of the withdrawal."""
        try:
            current_editor = WjsEditorAssignment.objects.get_current(self.workflow.article).editor
        except WjsEditorAssignment.DoesNotExist:
            current_editor = None
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=self.form_data.get("notification_subject"),
            message_body=self.form_data.get("notification_body"),
            actor=self.workflow.article.correspondence_author,
            recipients=[
                (current_editor if current_editor else communication_utils.get_eo_user(self.workflow.article))
            ],
        )

    def _check_typesetter_conditions(self) -> bool:
        """Check if there is an active TypesettingAssignment for the article."""
        return (
            TypesettingAssignment.objects.filter(
                round__article=self.workflow.article,
                completed__isnull=True,
            )
            .order_by("round__round_number")
            .last()
        )

    def _get_typesetter_context(self, assignment: TypesettingAssignment) -> Dict[str, Any]:
        return {
            "article": self.workflow.article,
            "recipient": assignment.typesetter,
        }

    def _log_typesetter(self, assignment: TypesettingAssignment):
        """Logs a message to the typesetter containing information about the withdrawal."""
        context = self._get_typesetter_context(assignment)
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="preprint_withdrawn_subject",
            journal=self.workflow.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="preprint_withdrawn_body",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=message_subject,
            message_body=message_body,
            recipients=[assignment.typesetter],
        )

    def run(self):
        with transaction.atomic():
            conditions = self._check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            self._close_review_assignments()
            self._update_state()
            self._log_supervisor()
            if assignment := self._check_typesetter_conditions():
                self._log_typesetter(assignment)
            return
