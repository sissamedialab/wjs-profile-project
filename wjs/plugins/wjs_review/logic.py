"""Business logic is here.

Most logic is encapsulated into dataclasses that take the necessary data structures upon creation and perform their
action in a method named "run()".

"""

import dataclasses
import datetime
import shutil
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET  # noqa
from copy import copy
from functools import cached_property
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional

import html2text
import requests
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# There are many "File" classes; I'll use core_models.File in typehints for clarity.
from core import files as core_files
from core import models as core_models
from core.models import AccountRole, Role, SupplementaryFile
from dateutil.parser import parse
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files import File
from django.core.files.uploadedfile import UploadedFile
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.db.models.query import FlatValuesListIterable
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django_fsm import can_proceed
from events import logic as events_logic
from journal.models import Journal
from plugins.wjs_submission.conversion import (
    TASK_LOG_PREFIX,
    get_feedback_logfile,
    report_yakunin_errors,
    report_yakunin_warnings,
)
from plugins.wjs_submission.models import (
    ArticleCollaboration,
    ArticleSubmission,
    RevisionArticleAuthorOrder,
    RevisionArticleCollaboration,
    RevisionStorage,
    RevisionSubmissionArticleFunding,
    SubmissionArticleFunding,
)
from review.logic import assign_editor, quick_assign
from review.models import ReviewRound
from review.views import upload_review_file
from submission.models import (
    STAGE_ASSIGNED,
    STAGE_UNDER_REVISION,
    Article,
    Field,
    FieldAnswer,
    FrozenAuthor,
)
from typesetting.models import TypesettingAssignment
from utils.logger import get_logger
from utils.setting_handler import get_setting

import wjs.jcom_profile.permissions
from wjs.jcom_profile import constants
from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.utils import (
    generate_token,
    get_eo_user,
    render_template_from_setting,
)

from . import communication_utils, permissions
from .events.assignment import dispatch_assignment
from .logic__production import (  # noqa: F401
    AssignTypesetter,
    AuthorSendsCorrections,
    BeginPublication,
    FinishPublication,
    HandleDownloadRevisionFiles,
    ReadyForPublication,
    RequestProofs,
    TypesettedFilesUpload,
    VerifyProductionRequirements,
)
from .models import (
    ArticleWorkflow,
    EditorDecision,
    EditorRevisionRequest,
    LatexPreamble,
    Message,
    PastEditorAssignment,
    PermissionAssignment,
    ProphyAccount,
    ProphyCandidate,
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
from .utils import (
    get_not_withdrawn_review_assignments_for_this_round,
    get_other_review_assignments_for_this_round,
    remove_existing_files_from_filesystem,
)

logger = get_logger(__name__)
Account = get_user_model()

states_when_article_is_considered_archived = [
    ArticleWorkflow.ReviewStates.WITHDRAWN,
    ArticleWorkflow.ReviewStates.REJECTED,
    ArticleWorkflow.ReviewStates.NOT_SUITABLE,
    ArticleWorkflow.ReviewStates.PUBLISHED,
]
# FIXME:this needs a broader refactoring probably
states_when_article_is_considered_archived_with_under_appeal = states_when_article_is_considered_archived + [
    ArticleWorkflow.ReviewStates.UNDER_APPEAL,
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

# "Working on" means articles that are actively in review i.e. they are assigned to some editor or a revision has been
# requested.
states_when_article_is_considered_working_on = [
    ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
    ArticleWorkflow.ReviewStates.TO_BE_REVISED,
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

states_where_article_is_considered_editor_completed = [
    ArticleWorkflow.ReviewStates.WITHDRAWN,
    ArticleWorkflow.ReviewStates.REJECTED,
    ArticleWorkflow.ReviewStates.INCOMPLETE_SUBMISSION,
    ArticleWorkflow.ReviewStates.NOT_SUITABLE,
    ArticleWorkflow.ReviewStates.ACCEPTED,
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
    ArticleWorkflow.ReviewStates.PROOFREADING,
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    ArticleWorkflow.ReviewStates.PUBLISHED,
    ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
    ArticleWorkflow.ReviewStates.SEND_TO_EDITOR_FOR_CHECK,
    ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS,
]

states_where_article_needs_eo_in_charge = [
    ArticleWorkflow.ReviewStates.EDITOR_TO_BE_SELECTED,
    ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
    ArticleWorkflow.ReviewStates.SUBMITTED,
    ArticleWorkflow.ReviewStates.TO_BE_REVISED,
    ArticleWorkflow.ReviewStates.ACCEPTED,
    ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
    ArticleWorkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES,
    ArticleWorkflow.ReviewStates.PROOFREADING,
    ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
    ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
    ArticleWorkflow.ReviewStates.SEND_TO_EDITOR_FOR_CHECK,
    ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS,
    ArticleWorkflow.ReviewStates.UNDER_APPEAL,
]

states_where_article_needs_editor = [
    ArticleWorkflow.ReviewStates.EDITOR_SELECTED,
    ArticleWorkflow.ReviewStates.SUBMITTED,
    ArticleWorkflow.ReviewStates.TO_BE_REVISED,
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


def handle_update_due_date_reminders(obj: EditorRevisionRequest | WorkflowReviewAssignment):
    """
    Update the reminder dates.

    Reminders are updated by recalculating the date based on the new target due date.

    - the new reminder's due date is in the future OR
    - reminder's new due date - reminder's sent date > clemency time
    """

    for reminder in Reminder.objects.filter(
        content_type=ContentType.objects.get_for_model(obj),
        object_id=obj.pk,
    ):
        reminder.update_due_date(obj.article.journal)


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
    actor: Account = None
    first_assignment: bool = False
    assignment_message: Optional[str] = None
    appeal: bool = False

    def _assign_editor(self) -> WjsEditorAssignment:
        assignment, _ = assign_editor(self.article, self.editor, "section-editor", request=self.request)
        # This converts EditorAssignment created by assign_editor to WjsEditorAssignment, by swapping the underlying
        # class and setting the id of the pointer field to the id of the original model.
        assignment_id = assignment.pk
        assignment.__class__ = WjsEditorAssignment
        assignment.editor_report_pdf_draft = None
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

    def _create_editor_should_select_reviewer_reminders_maybe(self, assignment: WjsEditorAssignment) -> bool:
        """
        Create reminders for the editor to select a reviewer.

        Reminders are created only if there is no not withdrawn assignment for the current review round.
        """
        not_withdrawn_assignments = get_not_withdrawn_review_assignments_for_this_round(
            assignment.article, assignment.article.current_review_round_object()
        )
        if not not_withdrawn_assignments.filter(date_declined__isnull=True).exists():
            EditorShouldSelectReviewerReminderManager(assignment.article, assignment.editor).create()
            return True
        return False

    def _create_editor_should_make_decision_reminders_maybe(self, assignment: WjsEditorAssignment) -> bool:
        """
        Create reminders for the editor to make a decision.

        Reminders are created only if there is at least one complete assignment for the current review round.
        """
        not_withdrawn_assignments = get_not_withdrawn_review_assignments_for_this_round(
            assignment.article, assignment.article.current_review_round_object()
        )
        # the condition is triggered only there is no incomplete assigment
        # and at least 1 completed with report assignment
        if (
            not not_withdrawn_assignments.filter(is_complete=False).exists()
            and not_withdrawn_assignments.filter(is_complete=True, date_accepted__isnull=False).exists()
        ):
            # ≊ article.active_reviews.
            # NB: don't use Janeway's article.active_reviews since it includes "withdrawn" reviews.
            EditorShouldMakeDecisionReminderManager(article=assignment.article, editor=assignment.editor).create()
            return True
        return False

    def _delete_director_reminders(self, assignment: WjsEditorAssignment):
        """Delete director's reminder."""
        DirectorShouldAssignEditorReminderManager(
            article=assignment.article,
        ).delete()

    def _get_message_context(self, assignment: WjsEditorAssignment) -> dict[str, Any]:
        return {
            "article": self.article,
            "request": self.request,
            "editor_assigment": assignment,
            "editor": self.editor,
            # We could pass along the request, which has all journal settings linked to it,
            # instead of hitting the DB for a specific setting,
            # but we prefer to decouple logic and request
            "default_editor_assign_reviewer_days": get_setting(
                setting_group_name="wjs_review",
                setting_name="default_editor_assign_reviewer_days",
                journal=self.article.journal,
            ).processed_value,
        }

    def _log_operation(self, context: dict[str, Any], assignment_message: Optional[str] = None):
        if not assignment_message:
            message_subject = render_template_from_setting(
                setting_group_name="email_subject",
                setting_name="subject_editor_assignment",
                journal=self.article.journal,
                request=self.request,
                context={
                    "article": self.article,
                },
                template_is_setting=True,
            )
            message_body = render_template_from_setting(
                setting_group_name="email",
                setting_name="editor_assignment",
                journal=self.article.journal,
                request=self.request,
                context=context,
                template_is_setting=True,
            )
        else:
            message_subject = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="editor_assignment_manual_subject",
                journal=self.article.journal,
                request=self.request,
                context={},
                template_is_setting=True,
            )
            message_body = self.assignment_message
        communication_utils.log_operation(
            article=self.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.actor,
            recipients=[self.editor],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            # Msgs to the editors are auto-read
            # this includes first assignments, new assignments, first ass. to guest eds
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def run(self) -> WjsEditorAssignment:
        with transaction.atomic():
            assignment = self._assign_editor()
            context = self._get_message_context(assignment=assignment)
            if not self.appeal:
                self._log_operation(context=context, assignment_message=self.assignment_message)
            select_reviewers_reminders = self._create_editor_should_select_reviewer_reminders_maybe(assignment)
            if not select_reviewers_reminders:
                self._create_editor_should_make_decision_reminders_maybe(assignment)
            self._delete_director_reminders(assignment)
        return assignment


@dataclasses.dataclass
class AssignToEditor:
    """
    Assigns an editor to an article and creates a review round to replicate the behaviour of janeway's move_to_review.

    request argument **must** have user attribute set to the current user.
    """

    editor: Account
    article: Article
    request: HttpRequest
    workflow: Optional[ArticleWorkflow] = None
    assignment: Optional[WjsEditorAssignment] = None
    first_assignment: bool = False
    assignment_message: Optional[str] = None
    appeal: bool = False

    def _create_workflow(self):
        self.workflow, __ = ArticleWorkflow.objects.get_or_create(
            article=self.article,
        )

    def _update_state(self):
        """Run FSM transition."""
        # In case of appeal the state transition is handled in the OpenAppeal logic class
        if self.appeal:
            return
        if can_proceed(self.workflow.director_selects_editor):
            self.workflow.director_selects_editor()
        else:
            self.workflow.editor_assign_different_editor()
        self.workflow.save()

    def _check_conditions(self) -> bool:
        is_section_editor = self.editor.check_role(self.request.journal, "section-editor", staff_override=False)
        state_condition_to_be_selected = can_proceed(self.workflow.director_selects_editor)
        state_condition_assign_different_editor = can_proceed(self.workflow.editor_assign_different_editor)
        # When EO opens appeals (for rejected papers), she chooses an editor and we can end up here.
        state_rejected = self.workflow.state == ArticleWorkflow.ReviewStates.REJECTED
        exist_other_assignments = (
            WjsEditorAssignment.objects.get_all(self.article).exclude(editor=self.editor).count() > 1
        )
        return (
            is_section_editor
            and (state_condition_to_be_selected or state_condition_assign_different_editor or state_rejected)
            and not exist_other_assignments
        )

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
                actor=self.request.user,
                first_assignment=self.first_assignment,
                assignment_message=self.assignment_message,
                appeal=self.appeal,
            ).run()
            self._update_state()
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
    form_data: dict[str, Any]
    request: HttpRequest
    assignment: Optional[WorkflowReviewAssignment] = None
    log_operation: bool = True

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

    def _reviewer_already_assigned(self) -> bool:
        """
        Check that the view has not already been called.

        Make sure the reviewer we are trying to assign isn't already assigned.
        :return: True if reviewer is already assigned, else False
        """
        return WorkflowReviewAssignment.objects.filter(
            article=self.workflow.article,
            reviewer=self.reviewer,
            is_complete=False,
        ).exists()

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
            default_report_form_answers = {}
            assignment_id = assignment.pk
            assignment.__class__ = WorkflowReviewAssignment
            assignment.reviewassignment_ptr_id = assignment_id
            assignment.report_form_answers = self.form_data.get("report_form_answers", default_report_form_answers)
            assignment.editor_invite_message = None
            assignment.tex_report_pdf = None
            assignment.shared_report = False
            assignment.save()
            # this is needed because janeway set assignment.due_date to a datetime object, even if the field is a date
            # by refreshing it from db, the value is casted to a date object
            assignment.refresh_from_db()
            self.update_author_note_visible(assignment)
        return assignment

    def update_author_note_visible(self, assignment: WorkflowReviewAssignment):
        # the cover letter data might be attached to Article (first version), in which case the reviewer must still see
        # the "primary" object, or to the (Editor)RevisionRequest in which case the review should not see the "primary"
        # object
        self.form_data.get("author_note_visible", True)
        cover_letter_object = assignment.version[0].cover_letter.object
        PermissionAssignment.objects.update_or_create(
            user=self.reviewer,
            content_type_id=ContentType.objects.get_for_model(cover_letter_object).pk,
            object_id=cover_letter_object.pk,
            permission=(
                PermissionAssignment.PermissionType.DENY
                if isinstance(cover_letter_object, EditorRevisionRequest)
                else PermissionAssignment.PermissionType.NO_NAMES
            ),
            defaults={
                "permission_secondary": (
                    PermissionAssignment.BinaryPermissionType.ALL
                    if self.form_data.get("author_note_visible", True)
                    else PermissionAssignment.BinaryPermissionType.DENY
                )
            },
        )

    def _get_message_context(self) -> dict[str, Any]:
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
            # note that this context is also used by the form to render the defalt message
            # so we cannot assume that the assignment (that may be fake) already has a reviewer
            "reviewer": self.form_data.get("reviewer", self.assignment.reviewer),
            "skip": False,
            "review_assignment": self.assignment,
            "acceptance_due_date": acceptance_due_date,
        }

    def _log_operation(self, context: dict[str, Any]):
        if self.reviewer == self.editor:
            message_verbosity = Message.MessageVerbosity.TIMELINE
            message_subject_setting = "wjs_editor_i_will_review_message_subject"
            message_body_setting = "wjs_editor_i_will_review_message_body"
            message_subject_setting_group_name = message_body_setting_group_name = "wjs_review"
        else:
            message_verbosity = Message.MessageVerbosity.FULL
            message_subject_setting = "subject_review_assignment"
            message_subject_setting_group_name = "email_subject"

            # do not confuse with review_assignement which is the default message presented to the editor for him to
            # modify
            message_body_setting = "review_invitation_message_body"
            message_body_setting_group_name = "wjs_review"

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

    def _create_reviewreport_reminders(self):
        """Create reminders related to writing the review report."""
        ReviewerShouldWriteReviewReminderManager(self.assignment).create()

    def _delete_editor_reminders(self):
        """Delete reminders for the editor, if a reviewer is pending, editor is free to wait for them."""
        EditorShouldMakeDecisionReminderManager(self.assignment.article, self.assignment.editor).delete()
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
            if self._reviewer_already_assigned():
                raise IntegrityError("Double request detected")
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
            if self.log_operation:
                self._log_operation(context=context)
            if self.reviewer == self.editor:
                self._create_reviewreport_reminders()
            else:
                self._create_reviewevaluate_reminders()
            self._delete_editor_reminders()
        return self.assignment


@dataclasses.dataclass
class EvaluateReview:
    """
    Handle the decision of the reviewer to accept / decline the review and checks the conditions for the transition.
    """

    assignment: WorkflowReviewAssignment
    reviewer: Account
    editor: Account
    form_data: dict[str, Any]
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
        messages.add_message(
            self.request,
            messages.SUCCESS,
            _(
                "Thank you for accepting to upload your review by %s. If you are ready to "
                "upload your review right now please fill in the form below, "
                'otherwise just exit the page and click "upload review" '
                "from the manuscript web page in due time."
            )
            % self.form_data["date_due"],
        )
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
        self._delete_reviewreport_reminders()
        handle_reviewer_deassignment_reminders(self.assignment)
        messages.add_message(self.request, messages.INFO, _("The invite to review has been declined."))
        if self.assignment.date_declined:
            return False

    def _delete_reviewevaluate_reminders(self):
        """Delete reminders related to the evaluation of this review request."""
        ReviewerShouldEvaluateAssignmentReminderManager(self.assignment).delete()

    def _delete_reviewreport_reminders(self):
        """Delete reminders related to writing the review report."""
        ReviewerShouldWriteReviewReminderManager(self.assignment).delete()

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

    def _get_postpone_too_far_in_the_future_message_context(self) -> dict[str, Any]:
        default_review_days = self.assignment.article.journal.get_setting(
            group_name="general",
            setting_name="default_review_days",
        )
        default_date_due = now().date() + datetime.timedelta(days=default_review_days)
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assignment": self.assignment,
            "reviewer": self.assignment.reviewer,
            "EO": get_eo_user(self.assignment.article),
            "editor": self.editor,
            "date_due": self.form_data["date_due"],
            "original_date_due": default_date_due,
        }

    def _get_accept_message_context(self) -> dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assignment": self.assignment,
            # NB: don't confuse with assignment.comments_for_editor
            "additional_comments": self.form_data["additional_comments"],
            "review_url": reverse("wjs_review_review", kwargs={"assignment_id": self.assignment.id}),
            # Please note that this same context is used also for notifications to the editor (who don't have access to
            # the `review_url` page)
        }

    def _get_decline_message_context(self) -> dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assignment": self.assignment,
            # NB: don't confuse with assignment.comments_for_editor
            "additional_comments": self.form_data["additional_comments"],
        }

    def _log_postpone_too_far_in_the_future(self):
        article = self.assignment.article
        journal = article.journal
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_far_future_subject",
            journal=journal,
            request=self.request,
            context={"reviewer": self.assignment.reviewer},
            template_is_setting=True,
        )
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
            recipients=[get_eo_user(article)],
        )

    def _log_accept(self):
        context = self._get_accept_message_context()
        self._log_editor_of_reviewer_acceptance(context)
        # when editor does I-will-review, the assignment is automatically accepted,
        # so he will never pass through here, and we can thank the reviewer safely
        self._log_reviewer_thanking_acceptance(context)

    def _log_editor_of_reviewer_acceptance(self, context: dict[str, Any]):
        # Warning: same setting as for _log_editor_of_reviewer_decline()
        # Warning: do not confuse settings
        # - reviewer_acknowledgement      (from rev to ed: rev accepts/declines assignment)
        # - review_accept_acknowledgement (from ed to rev: ed thanks rev for accepting)
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
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

    def _log_reviewer_thanking_acceptance(self, context: dict[str, Any]):
        # Warning: do not confuse settings
        # - reviewer_acknowledgement      (from rev to ed: rev accepts/declines assignment)
        # - review_accept_acknowledgement (from ed to rev: ed thanks rev for accepting)
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_accept_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_accept_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[self.assignment.reviewer],
            verbosity=Message.MessageVerbosity.EMAIL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
        )

    def _log_decline(self):
        context = self._get_accept_message_context()
        self._log_editor_of_reviewer_decline(context)
        self._log_reviewer_acknowledging_decline(context)

    def _log_editor_of_reviewer_decline(self, context):
        # Warning: same setting as for _log_editor_of_reviewer_acceptance()
        # Warning: do not confuse settings
        # - reviewer_acknowledgement      (from rev to ed: rev accepts/declines assignment)
        # - review_decline_acknowledgement (from ed to rev: ed thanks rev for decline)
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
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

    def _log_reviewer_acknowledging_decline(self, context):
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
            actor=None,
            recipients=[self.assignment.reviewer],
            verbosity=Message.MessageVerbosity.EMAIL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
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
    """Invite a user to do a review.

    Users to invite can be
    - existing users (with an Account and all)
    - new users (with name and email provided by the editor)
    - Prophy account (similar to "new users", but the data name and email etc. are taken from Prophy)
    """

    workflow: ArticleWorkflow
    editor: Account
    form_data: dict[str, Any]
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
        """Ensure that we have a real Account.

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

        # If the new user is related to a Prophy suggestion, we can delete that suggestion because we have already used
        # it. We know that the new user is related to a Prophy suggestion is the Prophy-account and the new user email
        # are the same.
        ProphyCandidate.objects.filter(
            article=self.workflow.article.id,
            prophy_account__email=user.email,
        ).delete()
        ProphyAccount.objects.filter(
            prophycandidate__isnull=True,
        ).delete()

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

    def _get_editor_message_context(self) -> dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "skip": False,
            "review_assignment": self.assignment,
        }

    def _get_reviewer_message_context(self) -> dict[str, Any]:
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
        context = self._get_reviewer_message_context()
        if self.assignment.reviewer != self.assignment.editor:
            self._thank_reviewer(context)
            self._notify_editor(context, verbosity=Message.MessageVerbosity.FULL)
        else:
            self._notify_editor(context, verbosity=Message.MessageVerbosity.TIMELINE)

    def _thank_reviewer(self, context: dict):
        """Send thank-you message to reviewer."""
        reviewer_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_complete_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        reviewer_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_complete_reviewer_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            # no actor as it's a system message
            article=self.assignment.article,
            message_subject=reviewer_message_subject,
            message_body=reviewer_message_body,
            recipients=[self.assignment.reviewer],
            verbosity=Message.MessageVerbosity.EMAIL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _notify_editor(self, context, verbosity):
        """Send notification to editor."""
        editor_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_review_complete_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        editor_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="review_complete_acknowledgement",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=self.assignment.reviewer,
            article=self.assignment.article,
            message_subject=editor_message_subject,
            message_body=editor_message_body,
            recipients=[self.assignment.editor],
            verbosity=verbosity,
            flag_as_read=False if verbosity == Message.MessageVerbosity.FULL else True,
            flag_as_read_by_eo=True,
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
class AuthorHandleRevisionObsolete:
    revision: EditorRevisionRequest
    form_data: dict[str, Any]
    user: Account  # TODO: not used? Please check and refactor!
    request: HttpRequest

    def _store_data(self):
        """Copy article metadata from intermediate EditorRevisionRequest to the article."""
        if self.revision.title:
            self.revision.article.title = self.revision.title
        if self.revision.abstract:
            self.revision.article.abstract = self.revision.abstract
        self.revision.article.save()

    def _confirm_revision(self):
        """Mark the revision as completed"""
        self.revision.date_completed = timezone.now()
        self.revision.save()

    @staticmethod
    def _trigger_complete_event(revision: EditorRevisionRequest, request: HttpRequest):
        """Trigger the ON_REVISIONS_COMPLETE event to comply with upstream review workflow."""
        kwargs = {
            "revision": revision,
            "request": request,
        }
        events_logic.Events.raise_event(events_logic.Events.ON_REVISIONS_COMPLETE, **kwargs)

    def _get_revision_submission_message_context(self) -> dict[str, Any]:
        self.editor = WjsEditorAssignment.objects.get_current(article=self.revision.article).editor
        return {
            "article": self.revision.article,
            "request": self.request,
            "skip": False,
            "revision": self.revision,
            "editor": self.editor,
            "default_editor_assign_reviewer_days": get_setting(
                setting_group_name="wjs_review",
                setting_name="default_editor_assign_reviewer_days",
                journal=self.revision.article.journal,
            ).processed_value,
        }

    def _was_under_appeal(self) -> bool:
        """Return True if the paper was under appeal."""
        return self.revision.type == ArticleWorkflow.Decisions.OPEN_APPEAL

    def _was_technical_revision(self) -> bool:
        """Return True if the revision was really a metadata-update."""
        return self.revision.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION

    def _notify_reviewers(self):
        """
        Send notifications to all reviewers of unsubmitted revisions.

        Unsubmitted reviews are available only in case of technical revisions, because for major / minor revisions
        reviewers are withdrawn when requesting the revision.
        """
        article = self.revision.article  # alias
        reviewer_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="technicalrevisions_complete_reviewer_notification_subject",
            journal=article.journal,
        ).processed_value

        context = self._get_revision_submission_message_context()
        # NB: don't use Janeway's article.active_reviews since it includes "withdrawn" reviews.
        current_round = article.current_review_round_object()
        for assignment in WorkflowReviewAssignment.objects.filter(
            article=article,
            review_round=current_round,
            is_complete=False,
        ).not_withdrawn():
            # customize message per-reviewer
            context["reviewer"] = assignment.reviewer
            reviewer_message_body = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="technicalrevisions_complete_reviewer_notification_body",
                journal=article.journal,
                request=self.request,
                context=context,
                template_is_setting=True,
            )

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
        # we need to render the subject too,
        # because it changes for major/minor and technical revision submissions
        reviewer_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_revisions_complete_editor_notification",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        reviewer_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="revisions_complete_editor_notification",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=None,
            article=self.revision.article,
            message_subject=reviewer_message_subject,
            message_body=reviewer_message_body,
            recipients=[self.revision.editor],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _notify_author(self):
        """Send a receipt notification to the author."""
        subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_revisions_complete_receipt",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        body = render_template_from_setting(
            setting_group_name="email",
            setting_name="revisions_complete_receipt",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=None,
            article=self.revision.article,
            message_subject=subject,
            message_body=body,
            recipients=[self.revision.article.correspondence_author],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
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
            recipients=[self.editor],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _log_operation(self):
        """Send notifications to editor and reviewers and a receipt to the author."""
        if self._was_under_appeal():
            self._notify_editor_with_appeal()
        else:
            self._notify_editor()
        self._notify_reviewers()
        if not self._was_technical_revision():
            self._notify_author()

    def _save_author_note(self):
        self.revision.author_note = self.form_data.get("author_note", "")
        self.revision.save()

    def _delete_author_reminders(self):
        if self._was_technical_revision():
            AuthorShouldSubmitTechnicalRevisionReminderManager(self.revision).delete()
        elif self.revision.type == ArticleWorkflow.Decisions.MAJOR_REVISION:
            AuthorShouldSubmitMajorRevisionReminderManager(self.revision).delete()
        elif self.revision.type == ArticleWorkflow.Decisions.MINOR_REVISION:
            AuthorShouldSubmitMinorRevisionReminderManager(self.revision).delete()

    def _create_editor_should_select_reviewer_reminders(self):
        """
        Create reminders for the editor to select a reviewer,
        when author submit a revision but not for appeal and technical revision.
        """
        if not self._was_under_appeal() and not self._was_technical_revision():
            EditorShouldSelectReviewerReminderManager(self.revision.article, self.revision.editor).create()

    def run(self):
        with transaction.atomic():
            self._store_data()
            self._confirm_revision()
            self._save_author_note()
            self._trigger_complete_event(self.revision, self.request)
            self._delete_author_reminders()
            self._create_editor_should_select_reviewer_reminders()
            self._log_operation()
            return self.revision


@dataclasses.dataclass()
class PopulateRevisionSteps:
    """Orchestrate the transfer of data from the revision storage to its proper place."""

    article: Article
    revision: EditorRevisionRequest
    revision_storage: RevisionStorage

    def run(self):
        PopulateRevisionStep1(
            article=self.article,
            revision=self.revision,
            revision_storage=self.revision_storage,
        ).run()
        if self.revision_storage.revision_flow_type in {
            RevisionStorage.RevisionFlowType.FULL,
            RevisionStorage.RevisionFlowType.METADATA,
        }:
            PopulateRevisionStep4(
                article=self.article,
                revision=self.revision,
                revision_storage=self.revision_storage,
            ).run()
        if self.revision_storage.revision_flow_type in {
            RevisionStorage.RevisionFlowType.FULL,
            RevisionStorage.RevisionFlowType.METADATA,
        }:
            PopulateRevisionStep5(
                article=self.article,
                revision=self.revision,
                revision_storage=self.revision_storage,
            ).run()
        if self.revision_storage.revision_flow_type == RevisionStorage.RevisionFlowType.FULL:
            PopulateRevisionStep6(
                article=self.article,
                revision=self.revision,
                revision_storage=self.revision_storage,
            ).run()
        if self.revision_storage.revision_flow_type == RevisionStorage.RevisionFlowType.FULL:
            PopulateRevisionStep7(
                article=self.article,
                revision=self.revision,
                revision_storage=self.revision_storage,
            ).run()
        self.revision.revision_flow_type = self.revision_storage.revision_flow_type
        _now = timezone.now()
        if (
            self.revision_storage.revision_flow_type == RevisionStorage.RevisionFlowType.FULL
            and self.revision.editor_decision.decision == ArticleWorkflow.Decisions.MAJOR_REVISION
        ):
            self.article.articleworkflow.last_major_revision = _now
            self.article.articleworkflow.save()
        self.revision.last_major_revision = _now


@dataclasses.dataclass()
class BasePopulateRevisionStep:
    article: Article
    revision: EditorRevisionRequest
    revision_storage: RevisionStorage

    def run(self):
        raise NotImplementedError()


class PopulateRevisionStep1(BasePopulateRevisionStep):
    def run(self):
        if cover_letter_note := self.revision_storage.data.get("comments_editor"):
            self.revision.author_note = cover_letter_note
        if file_id := self.revision_storage.data.get("cover_letter_file"):
            self.revision.cover_letter_file = core_models.File.objects.get(id=file_id)
        if new_competing_interests := self.revision_storage.data.get("competing_interests"):
            self.article.competing_interests = new_competing_interests
        # Additional submission fields
        for additional_submission_field in Field.objects.filter(journal=self.article.journal).order_by("order"):
            if additional_submission_field.name in self.revision_storage.data:
                # Using "update_or_create" (instead of just "update"), because the author could have filled some answer
                # that he had left empty during the first submission.
                FieldAnswer.objects.update_or_create(
                    article=self.article,
                    field=additional_submission_field,
                    defaults={"answer": self.revision_storage.data.get(additional_submission_field.name)},
                )


class PopulateRevisionStep4(BasePopulateRevisionStep):
    def run(self):
        if correspondence_author := self.revision_storage.data.get("correspondence_author"):
            self.article.correspondence_author_id = correspondence_author
        if authors_contributions := self.revision_storage.data.get("authors_contributions"):
            self.article.submission_data.authors_contributions = authors_contributions
            self.revision.authors_contributions = authors_contributions
        if owner := self.revision_storage.data.get("owner"):
            self.article.owner_id = owner
        if affiliation_country := self.revision_storage.data.get("affiliation_country"):
            self.article.submission_data.affiliation_country_id = affiliation_country

        FrozenAuthor.objects.filter(article=self.article).delete()
        article_authors = RevisionArticleAuthorOrder.objects.filter(revision_storage=self.revision_storage)
        for article_author in article_authors:
            FrozenAuthor.objects.create(
                article=self.article,
                author=article_author.author,
                order=article_author.order,
            )
        ArticleCollaboration.objects.filter(article=self.article).delete()
        article_collaborations = RevisionArticleCollaboration.objects.filter(revision_storage=self.revision_storage)
        for article_collaboration in article_collaborations:
            ArticleCollaboration.objects.create(
                article=self.article,
                collaboration=article_collaboration.collaboration,
                relation=article_collaboration.relation,
                order=article_collaboration.order,
            )


class PopulateRevisionStep5(BasePopulateRevisionStep):
    def run(self):
        if new_title := self.revision_storage.data.get("title"):
            self.revision.title = self.article.title
            self.article.title = new_title
        else:
            self.revision.title = self.article.title

        if new_abstract := self.revision_storage.data.get("abstract"):
            self.revision.abstract = self.article.abstract
            self.article.abstract = new_abstract
        else:
            self.revision.abstract = self.article.abstract
        if language := self.revision_storage.data.get("language"):
            self.article.language = language
        if section := self.revision_storage.data.get("section"):
            self.article.section_id = section


class PopulateRevisionStep6(BasePopulateRevisionStep):
    """Store cas, das and files."""

    def run(self):
        self.article.submission_data.cas = self.revision_storage.data.get("cas")
        self.article.submission_data.cas_url = self.revision_storage.data.get("cas_url")
        self.article.submission_data.das = self.revision_storage.data.get("das")
        self.article.submission_data.das_url = self.revision_storage.data.get("das_url")

        # Files
        # (remember that the files from the previous version have already been saved to revision-request
        # object when the revision was requested by the editor)
        self.article.manuscript_files.set([self.revision_storage.data["manuscript_files"]])
        self.article.source_files.set([self.revision_storage.data["source_files"]])

        # Data/figure (aka administrative) files and supplementary files:
        # let the article point directly to the ones selected during the revision, but do _not_ drop the rest
        # (because files from prevision versions must be kept)
        self.article.data_figure_files.set(self.revision_storage.data["data_figure_files"])
        # Remember the revision-storage only stores the pk of the File record;
        # the SupplementaryFile record does not yet exist.
        esm_files = [
            SupplementaryFile.objects.create(file_id=file_id)
            for file_id in self.revision_storage.data["supplementary_files"]
        ]
        self.article.supplementary_files.set([esm_file.pk for esm_file in esm_files])


class PopulateRevisionStep7(BasePopulateRevisionStep):
    def run(self):
        if access_mode := self.revision_storage.data.get("access_mode"):
            self.article.submission_data.access_mode_id = access_mode
        if special_request := self.revision_storage.data.get("special_request"):
            self.article.submission_data.special_request = special_request

        SubmissionArticleFunding.objects.filter(article=self.article).delete()
        fundings = RevisionSubmissionArticleFunding.objects.filter(revision_storage=self.revision_storage)
        for funding in fundings:
            SubmissionArticleFunding.objects.create(
                pk=funding.pk,
                article=self.article,
                name=funding.name,
                fundref_id=funding.fundref_id,
                funding_id=funding.funding_id,
                funding_statement=funding.funding_statement,
                country=funding.country,
            )


@dataclasses.dataclass
class AuthorHandleRevision:
    """
    Logic related to the submission of a revision.

    Note that this class triggers ON_REVISIONS_COMPLETE when done.
    """

    request: HttpRequest
    article: Article
    revision: EditorRevisionRequest = dataclasses.field(init=False)
    revision_storage: RevisionStorage = dataclasses.field(init=False)

    def __post_init__(self):
        """Define article and revision_storage instance variables."""
        self.revision = (
            EditorRevisionRequest.objects.filter(
                article=self.article,
                date_completed__isnull=True,
            )
            .order_by("-date_requested")
            .first()
        )
        self.revision_storage = RevisionStorage.objects.get(article=self.article)

    def _store_data(self):
        """
        Copy article metadata from temporary RevisionStorage to the article.

        Where necessary, keep the old value in EditorRevisionRequest.
        Also save answers from additional submission fields.
        """

        if not self.revision_storage.data.get("submission_requirements"):
            raise ValueError(
                "Author did not confirm submission requirements: "
                f"{self.article.submission_requirements=} / "
                f"{self.revision_storage.data.get('submission_requirements')}",
            )
        if not self.article.submission_requirements:
            # Some article might not have the submission-requirements checked.
            # This looks like a business-logic flow: if the author does not agree with the journal
            # requirements, why is he submitting a paper?
            # Logging this as a non-blocking error, becaues I'm not sure if/when this can happen.
            logger.error(
                f"Article {self.article.journal.code}_{self.article.id} has submission_requirements NOT set."
                " Please check. Proceeding anyway.",
            )

        # Keep history of data / snaphost data / versioning data
        # ====================
        #
        # Please remember that files (manuscript, source files, etc.) have already been "copied" to the
        # revision-request object, when the revision request was created.
        #
        PopulateRevisionSteps(
            article=self.article, revision=self.revision, revision_storage=self.revision_storage
        ).run()

        self.revision.save()
        self.article.submission_data.save()
        self.revision_storage.delete()
        self.article.save()

    def _confirm_revision(self):
        """
        Mark the revision as completed.

        The Article.stage / state are not changed here,
        but by event-handlers registered with the ON_REVISIONS_COMPLETE event.

        This allows for other code to hook-up to the event ON_REVISION_SUBMISSION_COMPLETED.
        """
        self.revision.date_completed = timezone.now()
        self.revision.save()

    @staticmethod
    def _trigger_complete_event(revision: EditorRevisionRequest, request: HttpRequest) -> None:
        """Trigger the ON_REVISIONS_COMPLETE event to comply with upstream review workflow."""
        kwargs = {
            "revision": revision,
            "request": request,
        }
        events_logic.Events.raise_event(events_logic.Events.ON_REVISIONS_COMPLETE, **kwargs)

    def _get_revision_submission_message_context(self) -> dict[str, Any]:
        self.editor = WjsEditorAssignment.objects.get_current(article=self.revision.article).editor
        return {
            "article": self.revision.article,
            "request": self.request,
            "skip": False,
            "revision": self.revision,
            "editor": self.editor,
            "default_editor_assign_reviewer_days": get_setting(
                setting_group_name="wjs_review",
                setting_name="default_editor_assign_reviewer_days",
                journal=self.revision.article.journal,
            ).processed_value,
        }

    def _was_under_appeal(self) -> bool:
        """Return True if the paper was under appeal."""
        return self.revision.type == ArticleWorkflow.Decisions.OPEN_APPEAL

    def _was_technical_revision(self) -> bool:
        """Return True if the revision was really a metadata-update."""
        return self.revision.type == ArticleWorkflow.Decisions.TECHNICAL_REVISION

    def _notify_reviewers(self):
        """
        Send notifications to all reviewers of unsubmitted revisions.

        Unsubmitted reviews are available only in case of technical revisions, because for major / minor revisions
        reviewers are withdrawn when requesting the revision.
        """
        article = self.revision.article  # alias
        reviewer_message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="technicalrevisions_complete_reviewer_notification_subject",
            journal=article.journal,
        ).processed_value

        context = self._get_revision_submission_message_context()
        # NB: don't use Janeway's article.active_reviews since it includes "withdrawn" reviews.
        current_round = article.current_review_round_object()
        for assignment in WorkflowReviewAssignment.objects.filter(
            article=article,
            review_round=current_round,
            is_complete=False,
        ).not_withdrawn():
            # customize message per-reviewer
            context["reviewer"] = assignment.reviewer
            reviewer_message_body = render_template_from_setting(
                setting_group_name="wjs_review",
                setting_name="technicalrevisions_complete_reviewer_notification_body",
                journal=article.journal,
                request=self.request,
                context=context,
                template_is_setting=True,
            )

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
        # we need to render the subject too,
        # because it changes for major/minor and technical revision submissions
        reviewer_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_revisions_complete_editor_notification",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        reviewer_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="revisions_complete_editor_notification",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=None,
            article=self.revision.article,
            message_subject=reviewer_message_subject,
            message_body=reviewer_message_body,
            recipients=[self.revision.editor],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _notify_author(self):
        """Send a receipt notification to the author."""
        subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_revisions_complete_receipt",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        body = render_template_from_setting(
            setting_group_name="email",
            setting_name="revisions_complete_receipt",
            journal=self.revision.article.journal,
            request=self.request,
            context=self._get_revision_submission_message_context(),
            template_is_setting=True,
        )
        communication_utils.log_operation(
            actor=None,
            article=self.revision.article,
            message_subject=subject,
            message_body=body,
            recipients=[self.revision.article.correspondence_author],
            verbosity=Message.MessageVerbosity.FULL,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
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
            recipients=[self.editor],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _log_operation(self):
        """Send notifications to editor and reviewers and a receipt to the author."""
        if self._was_under_appeal():
            self._notify_editor_with_appeal()
        else:
            self._notify_editor()
        self._notify_reviewers()
        if not self._was_technical_revision():
            self._notify_author()

    def _delete_author_reminders(self):
        if self._was_technical_revision():
            AuthorShouldSubmitTechnicalRevisionReminderManager(self.revision).delete()
        elif self.revision.type == ArticleWorkflow.Decisions.MAJOR_REVISION:
            AuthorShouldSubmitMajorRevisionReminderManager(self.revision).delete()
        elif self.revision.type == ArticleWorkflow.Decisions.MINOR_REVISION:
            AuthorShouldSubmitMinorRevisionReminderManager(self.revision).delete()

    def _create_editor_should_select_reviewer_reminders(self):
        """
        Create reminders for the editor to select a reviewer.

        When author submit a revision but not for appeal and technical revision.
        """
        if not self._was_under_appeal() and not self._was_technical_revision():
            EditorShouldSelectReviewerReminderManager(self.revision.article, self.revision.editor).create()

    def run(self):
        with transaction.atomic():
            self._store_data()
            self._confirm_revision()
            self._trigger_complete_event(self.revision, self.request)
            self._delete_author_reminders()
            self._create_editor_should_select_reviewer_reminders()
            self._log_operation()
            return self.revision


@dataclasses.dataclass
class DeselectReviewer:
    """
    Low-level logic to remove reviewer assignment.
    """

    assignment: WorkflowReviewAssignment
    actor: Account
    request: HttpRequest
    send_reviewer_notification: bool
    form_data: dict[str, Any]

    def _log_operation(self):
        """Log a message to the reviewer containing information about the motivation of the deassignment."""
        if self.send_reviewer_notification:
            verbosity = Message.MessageVerbosity.FULL
            message_body = self.form_data.get("notification_body")
            recipients = [self.assignment.reviewer]
        else:
            verbosity = Message.MessageVerbosity.TIMELINE
            message_body = ""
            recipients = [get_eo_user(self.assignment.article)]

        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=self.form_data.get("notification_subject"),
            message_body=message_body,
            verbosity=verbosity,
            actor=self.actor,
            recipients=recipients,
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    @staticmethod
    def _check_editor_conditions(assignment: WorkflowReviewAssignment, actor: Account) -> bool:
        """Check if the actor is the article's editor or the EO."""
        return is_article_editor_or_eo(assignment.article.articleworkflow, actor)

    def check_conditions(self):
        """Check if the conditions for the deassignment are met."""
        editor_conditions = self._check_editor_conditions(self.assignment, self.actor)
        return editor_conditions

    def _withdraw_assignment(self) -> bool:
        """
        Withdraw the assignment.
        """
        self._delete_reviewer_reminders()
        handle_reviewer_deassignment_reminders(self.assignment)
        self.assignment.withdraw()
        self._log_operation()
        return True

    def _delete_reviewer_reminders(self):
        """Delete all reminders for the deassigned reviewer."""

        ReviewerShouldWriteReviewReminderManager(self.assignment).delete()
        ReviewerShouldEvaluateAssignmentReminderManager(self.assignment).delete()

    def run(self) -> bool:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            success = self._withdraw_assignment()
            return success


@dataclasses.dataclass
class WithdrawIncompleteReviews:
    """
    Withdraw all incomplete review requests of an article.

    Use :py:class:`DeselectReviewer` to withdraw a single review request.
    """

    article: Article
    request: HttpRequest
    actor: Account
    subject_name: tuple[str, str] | None = None
    body_name: tuple[str, str] | None = None
    context: dict[str, Any] | None = None
    extra_filters: dict[str, Any] = None
    form_data: dict = None
    """If provided, use the form's data for the message body instead of getting it from a setting."""

    def check_conditions(self) -> bool:
        """Check if context and body_name and subject_name must be set."""
        content_settings_set = self.body_name and self.subject_name
        context_set = bool(self.context)
        return context_set and content_settings_set

    def _get_current_review_assignments(self) -> QuerySet[WorkflowReviewAssignment]:
        qs = WorkflowReviewAssignment.objects.filter(
            article=self.article,
            is_complete=False,
        )
        if self.extra_filters:
            qs = qs.filter(**self.extra_filters)
        return qs

    def _prepare_message(self, assignment: WorkflowReviewAssignment) -> tuple[str, str]:
        context = {**self.context, "recipient": assignment.reviewer}
        review_withdraw_subject = render_template_from_setting(
            setting_group_name=self.subject_name[1],
            setting_name=self.subject_name[0],
            journal=self.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        if self.form_data and "withdraw_notice" in self.form_data:
            review_withdraw_message = self.form_data["withdraw_notice"]
        else:
            review_withdraw_message = render_template_from_setting(
                setting_group_name=self.body_name[1],
                setting_name=self.body_name[0],
                journal=self.article.journal,
                request=self.request,
                context=context,
                template_is_setting=True,
            )
        return review_withdraw_message, review_withdraw_subject

    def run(self) -> list[WorkflowReviewAssignment]:
        conditions = self.check_conditions()
        if not conditions:
            raise ValueError(_("Transition conditions not met"))
        assignments = []
        with transaction.atomic():
            for assignment in self._get_current_review_assignments():
                message_body, message_subject = self._prepare_message(assignment)
                DeselectReviewer(
                    assignment=assignment,
                    actor=self.actor,
                    request=self.request,
                    send_reviewer_notification=True,
                    form_data={
                        "notification_subject": message_subject,
                        "notification_body": message_body,
                    },
                ).run()
                assignments.append(assignment)
        return assignments


@dataclasses.dataclass
class HandleDecision:
    workflow: ArticleWorkflow
    form_data: dict[str, Any]
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

    def __post_init__(self):
        """
        Perform a sanity check.

        Only technical-revisions decision (allow-metadata-changes) can be made without an editor report.
        """
        # TODO: this method will be removed in specs#1455
        if "decision_editor_report" not in self.form_data:
            self.form_data["decision_editor_report"] = ""
            if self.form_data["decision"] != ArticleWorkflow.Decisions.TECHNICAL_REVISION:
                # Allow the process to continue, but log an error.
                logger.error(
                    'Decision "%s" on AW %s without an editor report. Please check.',
                    self.form_data["decision"],
                    self.workflow.id,
                )

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

    def _trigger_article_event(self, event: str, context: dict[str, Any]):
        """Trigger the given event."""
        return events_logic.Events.raise_event(event, task_object=self.workflow.article, **context)

    def _get_message_context(
        self,
        revision: Optional[EditorRevisionRequest] = None,
    ) -> dict[str, Any]:
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

    def _log_accept(self, context: dict[str, Any]):
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
            flag_as_read_by_eo=True,
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
            flag_as_read_by_eo=True,
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
            flag_as_read_by_eo=True,
        )

    def _log_revision_request(self, context, revision_type=None):
        revision_request_message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_request_revisions",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        revision_request_message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="request_revisions",
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

    def _log_technical_revision_request(self, context: dict[str, str]):
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

    def _accept_article(self, decision: EditorDecision) -> Article:
        """
        Accept article.

        - Call janeway accept_article
        - Advance workflow state
        - Trigger ON_ARTICLE_ACCEPTED event

        :param decision: instance of the editor decision for the revision request
        :type decision: EditorDecision
        :return: instance of the article
        :rtype: Article
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

    def _decline_article(self, decision: EditorDecision) -> Article:
        """
        Decline article.

        The editor rejects the article, this action has nothing to do with the
        editor that does not want to work on this article anymore (editor declines
        the assignment).

        - Call janeway decline_article
        - Advance workflow state
        - Trigger ON_ARTICLE_DECLINED event

        :param decision: instance of the editor decision for the revision request
        :type decision: EditorDecision
        :return: instance of the article
        :rtype: Article
        """

        self.workflow.editor_writes_editor_report()
        self.workflow.editor_rejects_paper()
        self.workflow.save()

        context = self._get_message_context()
        self._withdraw_unfinished_review_requests(email_context=context)
        self._log_decline(context)
        self.workflow.article.decline_article()
        self._trigger_article_event(events_logic.Events.ON_ARTICLE_DECLINED, context)
        return self.workflow.article

    def _not_suitable_article(self, decision: EditorDecision) -> Article:
        """
        Mark article as not suitable.

        - Call janeway decline_article
        - Advance workflow state
        - Trigger ON_ARTICLE_DECLINED event

        :param decision: instance of the editor decision for the revision request
        :type decision: EditorDecision
        :return: instance of the article
        :rtype: Article
        """

        if self.admin_form:
            self.workflow.admin_deems_paper_not_suitable()
        else:
            self.workflow.editor_writes_editor_report()
            self.workflow.editor_deems_paper_not_suitable()
        self.workflow.save()

        context = self._get_message_context()
        self._withdraw_unfinished_review_requests(email_context=context)
        self._log_not_suitable(context)
        self.workflow.article.decline_article()
        self._trigger_article_event(events_logic.Events.ON_ARTICLE_DECLINED, context)
        return self.workflow.article

    def _requires_resubmission(self, decision: EditorDecision) -> Article:
        """
        Mark article as requires resubmission.

        - Change workflow state
        - Trigger ON_ARTICLE_DECLINED event

        :param decision: instance of the editor decision for the revision request
        :type decision: EditorDecision
        :return: instance of the article
        :rtype: Article
        """
        self.workflow.admin_or_system_requires_revision()
        self.workflow.save()

        context = self._get_message_context()
        self._withdraw_unfinished_review_requests(email_context=context)
        self._log_requires_resubmission(context)
        return self.workflow.article

    def _technical_revision_article(self, decision: EditorDecision) -> EditorRevisionRequest:
        """
        Ask for article technical revision.

        - Create EditorRevisionRequest
        - Store historical article metadata / files
        - Send notification to author

        :param decision: instance of the editor decision for the revision request
        :type decision: EditorDecision
        :return: instance of the revision request
        :rtype: EditorRevisionRequest
        """
        self.workflow.editor_writes_editor_report()
        self.workflow.editor_requires_a_revision()
        self.workflow.save()
        self.workflow.article.stage = STAGE_UNDER_REVISION
        self.workflow.article.save()
        revision = EditorRevisionRequest.objects.create(
            article=self.workflow.article,
            editor=self.user,
            type=ArticleWorkflow.Decisions.TECHNICAL_REVISION,
            date_requested=timezone.now(),
            date_due=self.form_data["date_due"],
            review_round=self.workflow.article.current_review_round_object(),
            editor_decision=decision,
        )
        self._assign_article_data(revision)
        context = self._get_message_context(revision)
        self._log_technical_revision_request(context)
        AuthorShouldSubmitTechnicalRevisionReminderManager(
            revision_request=revision,
        ).create()
        return revision

    def _revision_article(self, decision: EditorDecision) -> EditorRevisionRequest:
        """
        Ask for article revision.

        - Update workflow and article states
        - Creare EditorRevisionRequest

        :param decision: instance of the editor decision for the revision request
        :type decision: EditorDecision
        :return: instance of the revision request
        :rtype: EditorRevisionRequest
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
            editor_decision=decision,
        )
        self._assign_article_data(revision)
        context = self._get_message_context(revision)
        self._withdraw_unfinished_review_requests(email_context=context)
        self._trigger_article_event(events_logic.Events.ON_REVISIONS_REQUESTED_NOTIFY, context)
        time.sleep(0.2)
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

    def _assign_article_data(self, revision: EditorRevisionRequest):
        """Assign files to the revision request to keep track of the changes."""
        revision.manuscript_files.set(self.workflow.article.manuscript_files.all())
        revision.data_figure_files.set(self.workflow.article.data_figure_files.all())
        revision.supplementary_files.set(self.workflow.article.supplementary_files.all())
        revision.source_files.set(self.workflow.article.source_files.all())
        revision.title = self.workflow.article.title
        revision.abstract = self.workflow.article.abstract

        # We store the old Keywords' "word" instead of their ids. Doing so allows us to maintain a memory of the
        # original kwds even if they have been modified or deleted.
        revision.article_history = {
            "title": self.workflow.article.title,
            "abstract": self.workflow.article.abstract,
            "keywords": list(self.workflow.article.keywords.values_list("word", flat=True)),
        }
        revision.save()

    def _withdraw_unfinished_review_requests(self, email_context: dict[str, str]):
        """
        Mark unfinished review requests as withdrawn.
        """
        service = WithdrawIncompleteReviews(
            article=self.workflow.article,
            request=self.request,
            subject_name=("editor_deassign_reviewer_subject", "wjs_review"),
            body_name=("editor_deassign_reviewer_default", "wjs_review"),
            context=email_context,
            actor=self.user,
            form_data=self.form_data,
        )
        service.run()

    def _store_decision(self) -> EditorDecision:
        """Store decision information."""
        decision = EditorDecision.objects.create(
            workflow=self.workflow,
            editor=self.user,
            review_round=self.workflow.article.current_review_round_object(),
            decision=self.form_data["decision"],
            decision_editor_report=self.form_data["decision_editor_report"],
        )
        return decision

    def _handle_latex_pdf(self, decision):
        """If decision contains a report and it has a latex version, we save it in EditorDecision."""
        assignment = WjsEditorAssignment.objects.get_current(self.workflow.article)
        if self.form_data.get("decision_editor_report", []) and assignment.editor_report_pdf_draft:
            decision.decision_editor_report_pdf = assignment.editor_report_pdf_draft
            decision.save()

    def _mark_send_review_file(self):
        """Fix permissions on review-files for the author, based on the editor's selections."""
        send_review_file_pks = self.form_data.get("send_review_file", [])
        if send_review_file_pks:
            for pk, value in send_review_file_pks:
                assignment = WorkflowReviewAssignment.objects.get(pk=pk)
                self._update_send_review_file(assignment, value)

    def _update_send_review_file(self, assignment: WorkflowReviewAssignment, value: str):
        PermissionAssignment.objects.update_or_create(
            user=self.workflow.article.correspondence_author,
            content_type_id=ContentType.objects.get_for_model(assignment).pk,
            object_id=assignment.pk,
            permission=PermissionAssignment.PermissionType.DENY,
            defaults={
                "permission_secondary": (
                    PermissionAssignment.BinaryPermissionType.ALL
                    if value == "yes"
                    else PermissionAssignment.BinaryPermissionType.DENY
                )
            },
        )
        assignment.shared_report = value == "yes"
        assignment.save()

    def _get_editor_assignment(self) -> WjsEditorAssignment | None:
        """Return the current editor assignment (if any)."""
        try:
            # WjsEditorAssignment.objects.get_current raises an exception if there is no current assignment
            return WjsEditorAssignment.objects.get_current(self.workflow.article)
        except WjsEditorAssignment.DoesNotExist:
            return None

    def _delete_editor_reminders(self):
        """
        Delete all reminders for the editor.

        When the editor makes a decision, he is done.
        """
        if assigment := self._get_editor_assignment():
            EditorShouldMakeDecisionReminderManager(self.workflow.article, assigment.editor).delete()
            EditorShouldSelectReviewerReminderManager(self.workflow.article, assigment.editor).delete()

    def _delete_author_submission_log_files(self):
        """
        Remove conversion logs.

        During submission, the author's sources are converted to PDF and log files of the process are created.

        They are left around to allow us to debug eventual conversion errors not spotted during submission.
        We assume the they are not needed anymore as soon as the editor makes a decision.
        """
        [
            f.delete()
            for f in core_models.File.objects.filter(
                article_id=self.workflow.article.pk,
                original_filename__startswith=TASK_LOG_PREFIX,
            )
        ]
        self.workflow.article.submission_data.feedback_uuid = None
        self.workflow.article.submission_data.save()

    def run(self) -> EditorDecision:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Decision conditions not met"))
            decision = self._store_decision()
            self._handle_latex_pdf(decision)
            self._mark_send_review_file()
            handler = self._decision_handlers.get(self.form_data["decision"], None)
            if handler:
                getattr(self, handler)(decision)
                self._delete_editor_reminders()
            self._delete_author_submission_log_files()
            return decision


@dataclasses.dataclass
class PostponeRevisionRequestDueDate:
    """
    Business logic to postpone the value of EditorRevisionRequest.date_due.
    """

    revision_request: EditorRevisionRequest
    form_data: dict[str, Any]
    request: HttpRequest
    original_due_date: datetime.date
    """
    Storing original assignment date_due to calculate the difference because `EditorRevisionRequestDueDateForm` already
    updates `revision_request` instance.
    """

    def _check_postponed_date_due_too_far_future(self, original_due_date: datetime.date) -> bool:
        def new_date_greater_than_max_date(new_date_due: datetime.date, setting_name: str) -> bool:
            try:
                max_threshold = get_setting(
                    setting_group_name="wjs_review",
                    setting_name=setting_name,
                    journal=self.revision_request.article.journal,
                ).process_value()
                return original_due_date + datetime.timedelta(days=max_threshold) < new_date_due
            except ObjectDoesNotExist:
                logger.error(f"Setting wjs_review/{setting_name} is missing. Please check.")
                return False

        revision_type = self.revision_request.editor_decision.decision
        if revision_type == ArticleWorkflow.Decisions.MAJOR_REVISION:
            setting_name = "date_due_major_revisions_far_future_days"
        elif revision_type == ArticleWorkflow.Decisions.MINOR_REVISION:
            setting_name = "date_due_minor_revisions_far_future_days"
        elif revision_type == ArticleWorkflow.Decisions.OPEN_APPEAL:
            setting_name = "default_author_appeal_revision_days"
        else:
            logger.error(f"Programming error: due date on revisions of type {revision_type} cannot be changed")
            return False

        return new_date_greater_than_max_date(self.form_data["date_due"], setting_name)

    def _get_message_context(self, original_due_date: datetime.date) -> dict[str, Any]:
        return {
            "article": self.revision_request.article,
            "request": self.request,
            "EO": get_eo_user(self.revision_request.article),
            "editor": self.revision_request.editor,
            "date_due": self.form_data["date_due"],
            "original_due_date": original_due_date,
        }

    def _log_eo_date_due_too_far_future(self, context: dict[str, Any]):
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
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.revision_request.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.EMAIL,
            recipients=[get_eo_user(self.revision_request.article)],
        )

    def _log_author_if_date_due_is_postponed(self, context: dict[str, Any]):
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
            context=context,
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

    def _update_reminder_dates(self):
        """
        Update the reminder dates for the author.

        Unset reminders are move forward by the difference between the original due date and the postponed date.
        Sent reminders are moved forward by the same amount if they have been sent within the clemency days window.
        """
        handle_update_due_date_reminders(self.revision_request)

    def _save_date_due(self):
        self.revision_request.date_due = self.form_data["date_due"]
        self.revision_request.save()

    def check_date_conditions(self) -> bool:
        """Check if the date is in the future."""
        return self.form_data["date_due"] > timezone.localtime(timezone.now()).date()

    def check_conditions(self):
        """Check if the conditions for the assignment are met."""
        return self.check_date_conditions()

    def run(self):
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Decision conditions not met"))
            context = self._get_message_context(self.original_due_date)
            self._save_date_due()
            if self._check_postponed_date_due_too_far_future(self.original_due_date):
                self._log_eo_date_due_too_far_future(context)
            self._log_author_if_date_due_is_postponed(context)
            self._update_reminder_dates()


@dataclasses.dataclass
class HandleMessage:
    message: Message
    form_data: dict[str, Any]

    def __post_init__(self):
        if ContentType.objects.get_for_model(self.message.target) == Journal:
            raise NotImplementedError("🦆")

    @staticmethod
    def allowed_recipients_for_actor(actor: Account, article: Article) -> QuerySet[Account]:
        """Return the list of allowed recipients for the given actor.

        This method is used to build the queryset for the recipient ModelChoiceField in the MessageForm, and possibly
        other places.
        """

        def _get_directors(article_obj: Article) -> FlatValuesListIterable:
            """
            Get the director for the article journal.

            :param article_obj: Article
            :type article_obj: Article

            :return: List of director ids for the article journal.
            :rtype: FlatValuesListIterable
            """
            return Account.objects.filter(
                accountrole__journal=article_obj.journal,
                accountrole__role__slug=constants.DIRECTOR_ROLE,
            ).values_list("pk", flat=True)

        def _get_main_director(article_obj: Article) -> FlatValuesListIterable:
            """
            Get the main director for the article journal.

            :param article_obj: Article
            :type article_obj: Article

            :return: List of main director ids for the article journal.
            :rtype: FlatValuesListIterable
            """
            return Account.objects.filter(
                accountrole__journal=article_obj.journal,
                accountrole__role__slug=constants.DIRECTOR_MAIN_ROLE,
            ).values_list("pk", flat=True)

        def _get_correspondening_author(article_obj: Article) -> list[int]:
            """
            Get the corresponding author for the article.

            :param article_obj: Article
            :type article_obj: Article

            :return: List containing the correspondence author id
            :rtype: list[int]
            """
            return [article_obj.correspondence_author.pk]

        def _get_current_editor(article_obj: Article) -> list[int]:
            """
            Get the current editor for the article.

            :param article_obj: Article
            :type article_obj: Article

            :return: List of editor ids for current review round.
            :rtype: FlatValuesListIterable
            """
            try:
                return [WjsEditorAssignment.objects.get_current(article_obj).editor.pk]
            except WjsEditorAssignment.DoesNotExist:
                return []

        def _get_reviewers(article_obj: Article) -> FlatValuesListIterable:
            """
            Get the reviewers for any review assignment reviewer.

            :param article_obj: Article
            :type article_obj: Article

            :return: List of reviewers ids
            :rtype: FlatValuesListIterable
            """
            return article_obj.reviewassignment_set.all().values_list("reviewer", flat=True)

        def _get_connected_editors(article_obj: Article, reviewer: Account) -> FlatValuesListIterable:
            """
            Get the editor connected with any review assignment reviewer is assigned to.

            :param article_obj: Article
            :type article_obj: Article

            :param reviewer: Reviewer to check editors for
            :type reviewer: Account

            :return: List of editor ids
            :rtype: FlatValuesListIterable
            """
            return article_obj.reviewassignment_set.filter(reviewer=reviewer.id).values_list("editor", flat=True)

        def _get_past_editors(article_obj: Article) -> FlatValuesListIterable:
            """
            Get the editors connected with any review assignment reviewer is assigned to.

            :param article_obj: Article
            :type article_obj: Article

            :return: List of editor ids
            :rtype: FlatValuesListIterable
            """
            return article_obj.past_editor_assignments.values_list("editor", flat=True)

        def _get_typesetter(article_obj: Article) -> list[int]:
            """
            Get the typesetter of the last active typesetting assignment, if there's one.
            :param article_obj: Article
            :type article_obj: Article

            :return: List containing article's typesetter id
            :rtype: list[int]
            """
            if latest_ta := article_obj.articleworkflow.get_latest_typesetting_assignment(only_completed=False):
                return [latest_ta.typesetter.pk]
            return []

        allowed_recipients = Account.objects.all()
        # EO system user is always available
        users_pk = [get_eo_user(article).pk]

        articleworkflow = article.articleworkflow

        # EO can write to:
        if permissions.has_eo_role_by_article(instance=articleworkflow, user=actor):
            # NB: not to himself
            # an EO person is considered as already present in the list because of the EO system user

            # the Corresponding author
            users_pk.extend(_get_correspondening_author(article))
            # all the article's reviewers
            users_pk.extend(_get_reviewers(article))
            # current editor
            users_pk.extend(_get_current_editor(article))
            # past editors
            users_pk.extend(_get_past_editors(article))
            # the main director
            users_pk.extend(_get_main_director(article))
            # all directors
            users_pk.extend(_get_directors(article))
            # current typesetter
            users_pk.extend(_get_typesetter(article))
        # (all) Director(s) can write to:
        # (note that the Main-Director also has the "normal" Director role,
        #  but explicit is better than implicit)
        elif permissions.has_director_role_by_article(
            instance=articleworkflow,
            user=actor,
        ) or permissions.has_main_director_role_by_article(
            instance=articleworkflow,
            user=actor,
        ):
            # all the article's reviewers
            users_pk.extend(_get_reviewers(article))
            # current editor
            users_pk.extend(_get_current_editor(article))
            # past editors
            users_pk.extend(_get_past_editors(article))
            # all directors
            users_pk.extend(_get_directors(article))
            # the main director
            users_pk.extend(_get_main_director(article))
            if get_setting(
                "wjs_review",
                "author_can_contact_director",
                article.journal,
            ).processed_value:
                # the Corresponding author
                users_pk.extend(_get_correspondening_author(article))
        # Editor can write to:
        if permissions.is_article_editor(instance=articleworkflow, user=actor):
            # the journal's main director
            users_pk.extend(_get_main_director(article))
            # the Corresponding author
            users_pk.extend(_get_correspondening_author(article))
            # all the article's reviewers
            users_pk.extend(_get_reviewers(article))
        # Reviewers can write to:
        elif permissions.is_article_reviewer(instance=articleworkflow, user=actor):
            # the journal's main director
            users_pk.extend(_get_main_director(article))
            # "His" editor(s): only the editor that created the ReviewAssigment for this reviewer
            # I.e. not _all_ paper's editor. Other alternatives:
            # - all editors, e.g.: article.articleworkflow.get_editor_assignments()
            # - only the current/last editor
            users_pk.extend(_get_connected_editors(article, actor))
        # Author(s) can write to:
        elif permissions.is_article_author(instance=articleworkflow, user=actor):
            # the journal's main director (if permitted by the journal configuration)
            if get_setting(
                "wjs_review",
                "author_can_contact_director",
                article.journal,
            ).processed_value:
                users_pk.extend(_get_main_director(article))
            # current editor
            users_pk.extend(_get_current_editor(article))
        # Exclude the current user from the recipients list. It can happen in case of overlapping roles.
        users_pk = set(users_pk) - {actor.pk}
        return allowed_recipients.filter(pk__in=users_pk)

    @staticmethod
    def can_write_to(actor: Account, article: Article, recipient: Account) -> bool:
        """Check if the sender (:py:param: actor) can write to :py:param: recipient wrt this :py:param: article."""
        return HandleMessage.allowed_recipients_for_actor(actor, article).filter(pk=recipient.pk).exists()

    def run(self):
        """Save (and send) a message."""
        recipient = get_object_or_404(Account, id=self.form_data["recipient"])
        if not self.can_write_to(self.message.actor, self.message.target, recipient):
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
    ) -> dict[str, Any]:
        context = {
            "article": workflow.article,
            "request": self.request,
        }
        return context

    def _log_reassign(self, context: dict[str, str]):
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
        dispatch_assignment(article=self.workflow.article)
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
    Handle the decision to postpone the due date of the reviewer acceptance / report.
    """

    assignment: WorkflowReviewAssignment
    editor: Account
    user: Account
    form_data: dict[str, Any]
    request: HttpRequest
    original_due_date: datetime.date
    """
    Storing original assignment date_due to calculate the difference because `UpdateReviewerDueDateForm` already
    updates `assignment` instance.
    """

    def _report_postponed_far_future_date(self) -> bool:
        """Check if the editor postponed due date far in the future."""
        if self.form_data["date_due"] > self.original_due_date + datetime.timedelta(
            days=settings.REVIEW_REQUEST_DATE_DUE_MAX_THRESHOLD,
        ):
            return True

    def _get_message_context(self) -> dict[str, Any]:
        return {
            "article": self.assignment.article,
            "request": self.request,
            "review_assigment": self.assignment,
            "reviewer": self.assignment.reviewer,
            "EO": get_eo_user(self.assignment.article),
            "editor": self.editor,
            "date_due": self.form_data["date_due"],
            "original_due_date": self.original_due_date,
        }

    def _log_reviewer_if_date_is_postponed(self) -> None:
        """Log a warning for the reviewer if the due date is postponed."""
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_postpone_subject",
            journal=self.assignment.article.journal,
            request=self.request,
            context={"reviewer": self.assignment.reviewer, "review_assigment": self.assignment},
            template_is_setting=True,
        )
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
            actor=self.user,
            recipients=[self.assignment.reviewer],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _log_eo_far_future_date(self) -> None:
        """Log a warning for the EO if the editor postponed due date far in the future."""
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="due_date_far_future_subject",
            journal=self.assignment.article.journal,
            request=self.request,
            context={"reviewer": self.assignment.reviewer, "review_assigment": self.assignment},
            template_is_setting=True,
        )
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
            recipients=[get_eo_user(self.assignment.article)],
        )

    def _save_reviewer_date_due(self):
        """
        Set and save the postponed date_due.
        """
        self.assignment.date_due = self.form_data.get("date_due")
        self.assignment.save()
        self._update_reminder_dates()

    def _update_reminder_dates(self):
        """
        Update the reminder dates for the reviewer.

        Unset reminders are move forward by the difference between the original due date and the postponed date.
        Sent reminders are moved forward by the same amount if they have been sent within the clemency days window.
        """
        handle_update_due_date_reminders(self.assignment)

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
class SupervisorChangeEditorAssignment:
    article: Article
    assignment: WjsEditorAssignment
    new_editor: Account
    request: HttpRequest
    deassignment_message: Optional[str] = None
    assignment_message: Optional[str] = None
    appeal: bool = False

    def _deassign_current_editor(self) -> Account:
        """Deassigns the current editor using existing :py:class:`BaseDeassignEditor` logic."""
        past_assignment = BaseDeassignEditor(
            assignment=self.assignment,
            editor=self.assignment.editor,
            request=self.request,
        ).run()
        if not self.appeal:
            self._log_past_editor()
        return past_assignment.editor

    def _log_past_editor(self):
        """Log a message to the deassigned Editor."""
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="subject_unassign_editor",
            journal=self.assignment.article.journal,
            request=self.request,
            context={},
            template_is_setting=True,
        )
        message_body = self.deassignment_message
        communication_utils.log_operation(
            article=self.assignment.article,
            message_subject=message_subject,
            message_body=message_body,
            verbosity=Message.MessageVerbosity.FULL,
            actor=self.request.user,
            recipients=[self.assignment.editor],
            hijacking_actor=wjs.jcom_profile.permissions.get_hijacker(),
            notify_actor=communication_utils.should_notify_actor(),
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def _assign_new_editor(self) -> WjsEditorAssignment:
        """Assigns newly selected editor using our existing logic."""
        return AssignToEditor(
            editor=self.new_editor,
            article=self.article,
            request=self.request,
            assignment_message=self.assignment_message,
            appeal=self.appeal,
        ).run()

    def _migrate_review_assignments(self, old_editor: Account):
        """
        Migrate review assignments from the old editor to the new editor.

        Replace editor for existing review assignments for the current review round and assign permissions to the old
        editor on completed review assignments.
        """
        base_qs = WorkflowReviewAssignment.objects.filter(
            article=self.assignment.article, review_round=self.assignment.review_rounds.first()
        )
        base_qs.filter(editor=old_editor).update(editor=self.new_editor)
        assignments = base_qs.filter(editor=self.new_editor)
        for assignment in assignments:
            if assignment.is_complete:
                PermissionAssignment.objects.create(
                    content_type_id=ContentType.objects.get_for_model(assignment).pk,
                    object_id=assignment.pk,
                    user=old_editor,
                    permission=PermissionAssignment.PermissionType.ALL,
                    permission_secondary=PermissionAssignment.BinaryPermissionType.ALL,
                )
            self._migrate_assignment_reminders(old_editor, assignment)

    def _migrate_assignment_reminders(self, old_editor: Account, assignment: WorkflowReviewAssignment):
        """
        Migrate reminders from the old editor to the new editor.

        Replace editor for unsent reminders for the current article.
        """
        for reminder in Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(assignment),
            object_id=assignment.pk,
            date_sent__isnull=True,
            recipient=old_editor,
        ):
            reminder.update_recipient(self.new_editor)
        for reminder in Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(assignment),
            object_id=assignment.pk,
            date_sent__isnull=True,
            actor=old_editor,
        ):
            reminder.update_actor(self.new_editor)

    def _migrate_article_reminders(self, old_editor: Account):
        """
        Migrate reminders from the old editor to the new editor.

        Replace editor for unsent reminders for the current article.
        """
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(self.article),
            object_id=self.article.pk,
            date_sent__isnull=True,
            recipient=old_editor,
        ).update(recipient=self.new_editor)
        Reminder.objects.filter(
            content_type=ContentType.objects.get_for_model(self.article),
            object_id=self.article.pk,
            date_sent__isnull=True,
            actor=old_editor,
        ).update(actor=self.new_editor)

    def run(self):
        with transaction.atomic():
            old_editor = self.assignment.editor
            self._migrate_review_assignments(old_editor)
            self._migrate_article_reminders(old_editor)
            self._deassign_current_editor()
            new_assignment = self._assign_new_editor()
            return new_assignment


@dataclasses.dataclass
class HandleEditorDeclinesAssignment:
    """
    Handle disassociation of an editor from an article followed by a declination of editor assignment.
    """

    assignment: WjsEditorAssignment
    editor: Account
    request: HttpRequest
    form_data: dict[str, Any]
    director: Optional[Account] = None
    """
    Director is loaded at runtime to send decline notifications. It's not meant to be set when initializing the class.
    """

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "editor": self.editor,
            "director": self.director,
            "article": self.assignment.article,
            "decline_reason": dict(PastEditorAssignment.DeclineReasons.choices).get(
                self.form_data.get("decline_reason"),
            ),
            "decline_text": self.form_data.get("decline_text", None),
        }

    def _log_director(self):
        """Log a message to the Director containing information about the motivation of the declination."""
        context = self._get_message_context()
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="editor_decline_assignment_subject",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="editor_decline_assignment_default",
            journal=self.assignment.article.journal,
            request=self.request,
            context=context,
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

    def _withdraw_unfinished_review_requests(self):
        """Mark unfinished review requests as withdrawn."""
        service = WithdrawIncompleteReviews(
            article=self.assignment.article,
            request=self.request,
            subject_name=("editor_decline_assignment__for_reviewers_subject", "wjs_review"),
            body_name=("editor_decline_assignment__for_reviewers_body", "wjs_review"),
            context=self._get_message_context(),
            actor=self.editor,
            form_data={},
        )
        service.run()

    def _update_state(self):
        self.assignment.article.articleworkflow.ed_declines_assignment()
        self.assignment.article.articleworkflow.save()

    def _create_director_reminder(self):
        """Create a reminder for the director."""
        DirectorShouldAssignEditorReminderManager(
            article=self.assignment.article,
        ).create()

    def _save_decline_info(self, past_assignment: PastEditorAssignment):
        past_assignment.decline_reason = self.form_data["decline_reason"]
        past_assignment.decline_text = self.form_data.get("decline_text", None)
        past_assignment.save()

    def run(self) -> PastEditorAssignment:
        with transaction.atomic():
            self._withdraw_unfinished_review_requests()
            past_assignment = BaseDeassignEditor(self.assignment, self.editor, self.request).run()
            self._save_decline_info(past_assignment)
            self._create_director_reminder()
            self._update_state()
            self.director = communication_utils.get_director_user(self.assignment.article)
            if self.request.user == self.editor and self.director:
                self._log_director()
            return past_assignment


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
        return self.article.author_accounts.filter(id=self.new_editor.id).exists()

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
        appeal_revision_days = get_setting(
            setting_group_name="wjs_review",
            setting_name="default_author_appeal_revision_days",
            journal=self.article.journal,
        ).process_value()
        form_data = {
            "decision": ArticleWorkflow.Decisions.OPEN_APPEAL,
            "decision_editor_report": "",
            "acceptance_due_date": None,
            "date_due": timezone.now().date() + datetime.timedelta(days=appeal_revision_days),
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
            # refs https://gitlab.sissamedialab.it/wjs/specs/-/work_items/1469
            flag_as_read_by_eo=True,
        )

    def update_editor(self):
        """
        Update the editor assignment.

        Use :py:class:`SupervisorChangeEditorAssignment` to update the editor assignment.
        """
        SupervisorChangeEditorAssignment(
            article=self.article,
            assignment=WjsEditorAssignment.objects.get_current(self.article),
            new_editor=self.new_editor,
            request=self.request,
            appeal=True,
        ).run()

    def run(self):
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            if not self._is_current_editor(self.article, self.new_editor):
                self.update_editor()
            self._handle_decision()
            self._log_author()


@dataclasses.dataclass
class WithdrawPreprint:
    """Withdraw manuscript."""

    workflow: ArticleWorkflow
    request: HttpRequest
    form_data: dict[str, Any]

    def _check_user_conditions(self) -> bool:
        """Check if the user is the correspondence author or owner."""
        return self.request.user in [self.workflow.article.correspondence_author, self.workflow.article.owner]

    def _has_past_rejection(self) -> bool:
        """Check if the article was already rejected one time."""
        return EditorDecision.objects.filter(
            workflow=self.workflow,
            decision=ArticleWorkflow.Decisions.REJECT,
        ).exists()

    def _check_state_conditions(self) -> bool:
        """Check if the FSM transition can be made."""
        withdraw_without_rejection = (
            can_proceed(self.workflow.author_or_owner_withdraws_preprint) and not self._has_past_rejection()
        )
        withdraw_after_a_rejection = (
            can_proceed(self.workflow.author_or_owner_withdraws_preprint_after_a_rejection)
            and self._has_past_rejection()
        )
        return withdraw_without_rejection or withdraw_after_a_rejection

    def _check_conditions(self) -> bool:
        """Check if the conditions for the withdrawal are met."""
        return self._check_user_conditions() and self._check_state_conditions()

    def _close_review_assignments(self):
        """Close all the review assignments and log reviewers."""
        service = WithdrawIncompleteReviews(
            article=self.workflow.article,
            request=self.request,
            subject_name=("preprint_withdrawn_subject", "wjs_review"),
            body_name=("preprint_withdrawn_body", "wjs_review"),
            context={"article": self.workflow.article},
            actor=get_eo_user(self.workflow.article),
        )
        service.run()

    def _update_state(self):
        """Run FSM transition."""
        if self._has_past_rejection() and can_proceed(
            self.workflow.author_or_owner_withdraws_preprint_after_a_rejection
        ):
            self.workflow.author_or_owner_withdraws_preprint_after_a_rejection()
        else:
            self.workflow.author_or_owner_withdraws_preprint()
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
            recipients=[current_editor if current_editor else get_eo_user(self.workflow.article)],
        )

    def _get_editor_assignment(self) -> WjsEditorAssignment | None:
        """Return the current editor assignment (if any)."""
        try:
            # WjsEditorAssignment.objects.get_current raises an exception if there is no current assignment
            return WjsEditorAssignment.objects.get_current(self.workflow.article)
        except WjsEditorAssignment.DoesNotExist:
            return None

    def _get_typesetting_assignment(self) -> TypesettingAssignment | None:
        """Return the current typesetting assignment (if any)."""
        return self.workflow.get_latest_typesetting_assignment(only_completed=False)

    def _get_typesetter_context(self, assignment: TypesettingAssignment) -> dict[str, Any]:
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

    def _delete_editor_reminders(self):
        """
        Delete all reminders for the editor.

        If a paper is withdrawn, no need for reminders.

        Reviewer reminders are deleted when withdrawing the review assignment.
        """
        if assignment := self._get_editor_assignment():
            EditorShouldMakeDecisionReminderManager(self.workflow.article, assignment.editor).delete()
            EditorShouldSelectReviewerReminderManager(self.workflow.article, assignment.editor).delete()

    def run(self):
        with transaction.atomic():
            conditions = self._check_conditions()
            if not conditions:
                raise ValueError(_("Transition conditions not met"))
            self._close_review_assignments()
            # handler initialized before state update
            handler = PressNotificationHandler(self.workflow, self.request)
            self._update_state()
            self._log_supervisor()
            self._delete_editor_reminders()
            if assignment := self._get_typesetting_assignment():
                self._log_typesetter(assignment)
            handler.run()
            return


class YakuninPDFGenerationError(Exception):
    """Raised when Yakunin fails to generate the PDF due to content or processing issues."""


class YakuninPDFGenerationWarnings(Warning):
    """Raised when Yakunin generate a PDF but report conversion warnings."""


class YakuninRequestError(Exception):
    """Raised when the request to Yakunin fails (network, timeout, HTTP errors)."""


@dataclasses.dataclass
class YakuninClient:
    """Interact with Yakunin."""

    file: bytes
    filename: str
    log: str | None = None
    "Content of the yakunin-task.log file."

    def _handle_mock_file(self):
        """
        Handles the processing of a mock file designated by the YAKUNIN_MOCK_FILE
        setting. The file is processed by reading its content, unpacking it, and
        returning a temporary directory along with a predefined message. Thi is
        returned instead of a file deriving from the real execution.

        :return: A tuple containing the temporary directory created during unpacking
                 and a string message simulating a successful log.
        :rtype: tuple
        """
        with open(settings.YAKUNIN_MOCK_FILE, "rb") as mock_file:
            tmpdir = self._unpack_response_file(mock_file.read())
            return tmpdir, "No logs for mock file"

    def _request(
        self,
        endpoint: str,
        files: dict[str, tuple[str, bytes]],
        data: Mapping[str, str] | None = None,
    ) -> tuple[Path, str]:
        """
        Send a POST request to the specified endpoint with files and data.

        Processes the response, and handles potential issues such as network errors or problems with the response
        content. The method is specifically used for interactions with the Yakunin system for generating PDFs.

        :param endpoint: Endpoint URL path to which the request is sent.
        :type endpoint: str
        :param files: Dictionary of files to send in the request. Keys represent file field names, and
                      values are tuples containing the file name and the file's content in bytes.
        :type files: dict[str, tuple[str, bytes]]
        :param data: Optional dictionary of data to send in the request. Each key-value pair represents
                     form data sent along with the files.
        :type data: Optional[Mapping[str, str]]
        :return: A tuple containing the path to the unpacked directory (as a Path object) and the
                 Yakunin log string extracted from the response.
        :rtype: tuple[Path, str]
        :raises YakuninRequestError: If there is an issue with the network connection, the request times out,
                                     or the server returns an HTTP error.

        """
        if getattr(settings, "YAKUNIN_MOCK_FILE", None):
            tmpdir, log = self._handle_mock_file()
            return tmpdir, log

        url = settings.YAKUNIN_URL + endpoint
        try:
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            raise YakuninRequestError(str(e)) from e

        tmpdir = self._unpack_response_file(response.content)
        # The caller can have fast access to the conversion log through this attribute:
        self.log = self._extract_yakunin_logs(tmpdir)
        return tmpdir, self.log

    # This method is not currently used but could be useful
    def call_yakunin_mkpdf(self) -> tuple[Path, str]:
        """
        Call the 'mkpdf/' endpoint.

        Generate a PDF file using the provided file data and filename.

        :return: The path to the generated PDF file.
        :rtype: Path
        """
        files = {
            "file": (
                self.filename,
                self.file,
            ),
        }
        return self._request("mkpdf/", files)

    def call_yakunin_watermark(self, ini_file: BytesIO, feedback_ws_url: str | None = None) -> tuple[Path, str]:
        """
        Calls the Yakunin watermark service.

        This method sends files and their configuration settings to a specific Yakunin
        watermarking service endpoint. Depending on whether a feedback WebSocket URL
        is provided, it either interacts with the feedback-enabled endpoint or the
        basic one. The method processes the input files, constructs the necessary
        payload, and internally calls the service.

        :param ini_file: Configuration file provided as a BytesIO object for the
            watermarking process.
        :type ini_file: BytesIO
        :param feedback_ws_url: WebSocket URL string for processing feedback data
            during watermarking.
        :type feedback_ws_url: str
        :return: A tuple containing the resulting file path and a response string
            from the watermarking service.
        :rtype: tuple[Path, str]
        """
        files = {
            "file": (
                self.filename,
                self.file,
            ),
            "ini": (ini_file.name, ini_file.getvalue()),
        }

        if feedback_ws_url:
            return self._request("watermark/ws/", files, {"feedback_ws_url": feedback_ws_url})
        return self._request("watermark/", files)

    @staticmethod
    def _unpack_response_file(content: bytes) -> Path:
        """
        Unpack the content of a tar.gz file into a temporary directory.

        This function creates a temporary directory, unpacks the provided compressed
        content (in tar.gz format) into it, and returns the path of the created
        temporary directory.

        :param content: The tar.gz file content to unpack.
        :type content: bytes
        :return: Path to the temporary directory where the content is unpacked.
        :rtype: Path
        """
        unpack_dir = tempfile.mkdtemp()

        with BytesIO(content) as file_obj, tarfile.open(fileobj=file_obj, mode="r:gz") as tar:
            tar.extractall(path=unpack_dir)
        return Path(unpack_dir)

    @staticmethod
    def _extract_yakunin_logs(tmpdir: Path) -> str:
        """
        Extract the content of a Yakunin conversion log file.

        Note that the filename is hardoced here, and must agree with
        yakunin.utils.TASK_LOGFILE_NAME.

        :param tmpdir: A pathlib.Path object pointing to the temporary directory where
                       the Yakunin log file is expected to reside.
        :type tmpdir: Path
        :return: The decoded content of the Yakunin log file as a string.
        :rtype: str

        """
        yakunin_log_file = next(tmpdir.glob("yakunin-task.log"), None)
        return Path(yakunin_log_file).read_text(encoding="utf-8")

    @staticmethod
    def no_errors_in_log(log: str) -> bool:
        """
        Analyze the given log for critical or error-level messages.

        This function inspects each line of the given log string. If it encounters
        a line that starts with "ERROR" or "CRITICAL", it sets a flag indicating
        the presence of such messages and stops further inspection. The final
        decision is then returned as a boolean value indicating the absence of
        "errors" or "critical" messages.

        :param log: The log string to be analyzed line by line.
        :type log: str
        :return: A boolean value indicating the absence of "ERROR" or
            "CRITICAL" messages. Returns True if no such messages exist,
            otherwise False.
        :rtype: bool
        """
        has_error_or_critical = False

        for line in log.splitlines():
            if line.startswith(("ERROR", "CRITICAL")):
                has_error_or_critical = True
                break
        return not has_error_or_critical

    # TODO: move to wjs_submission / merge with Yakunin's TaskLogger.conversion_result() ?
    @staticmethod
    def conversion_result(log: str) -> str:
        """
        Give a short summary of the result of the conversion.

        Returns:
          error: if the conversion failed (one should not expect to find a PDF)
          warning: if the PDF was generated, but there were some errors/warnings in the process
          success: if the PDF was generated without errors/warnings

        """
        # We could have ERROR and WARNING in the same log;
        # ERROR takes precedence: as soon as we find an error we don't care about warnings anymore;
        # also, one or many WARNINGs are equivalent.
        result = None
        for line in log.splitlines():
            if line.startswith(("ERROR", "CRITICAL")):
                result = "error"
                break
            if not result and line.startswith("WARNING"):
                result = "warning"
            if line.startswith("⇧ Task log end."):
                # This is a well-known boundary:
                # see Yakunin's Archive.submission_archive()
                break
        if not result:
            result = "success"
        return result


@dataclasses.dataclass
class PressNotificationHandler:
    """Send notification to journal press if required."""

    workflow: ArticleWorkflow
    request: HttpRequest

    def __post_init__(self):
        self._press_to_be_notified = self.workflow.state in states_when_article_is_considered_in_production

    def run(self) -> None:
        if self._press_to_be_notified:
            self.notification_journalpress()

    def notification_journalpress(self) -> None:
        """Send notification to journal press via email when article is witdrawn after acceptance."""
        article = self.workflow.article
        try:
            recipients = settings.WJS_ARTICLE_WITHDRAWN_PRESS_NOTIFICATION_EMAILS[article.journal.code]
            enabled = settings.WJS_ARTICLE_WITHDRAWN_PRESS_NOTIFICATION_ENABLED[article.journal.code]
            if not enabled:
                return
        except (AttributeError, KeyError) as e:
            logger.error(
                f"Article withdrawn after acceptance in {article.journal.code}, but no press email sent because "
                f"WJS_ARTICLE_WITHDRAWN_PRESS_NOTIFICATION_EMAILS or "
                f"WJS_ARTICLE_WITHDRAWN_PRESS_NOTIFICATION_ENABLED are not properly set: {e}"
            )
            return
        authors_string = self.workflow.article_authors_string
        context = {
            "article": article,
            "authors_string": authors_string,
        }
        message_subject = render_template_from_setting(
            setting_group_name="email_subject",
            setting_name="article_withdrawn_press_subject",
            journal=article.journal,
            request=self.request,
            context=context,
        )
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="article_withdrawn_press_body",
            journal=article.journal,
            request=self.request,
            context=context,
        )
        message_body_text = html2text.html2text(message_body)
        from_email = get_setting("general", "from_address", article.journal).processed_value
        send_mail(
            subject=message_subject,
            message=message_body_text,
            from_email=from_email,
            recipient_list=recipients,
            html_message=message_body,
        )


@dataclasses.dataclass
class ConvertManuscriptToPdf:
    """
    Convert an article's manuscript sources to PDF.

    This class deals differently with new submissions and revisions.
    """

    article_id: int
    file_id: int
    feedback_ws_url: str | None
    feedback_ws_name: str | None
    feedback_uuid: uuid.UUID | None

    is_revision: bool = False

    @cached_property
    def article(self) -> Article:
        return Article.objects.get(pk=self.article_id)

    def _report_result_via_ws(self, result: str, text: str, log_url: str):
        if not self.feedback_ws_name:
            return
        channel_layer = get_channel_layer()
        payload = {
            "result": result,
            "text": text,
            "data": log_url,
        }
        logger.debug(f"Report success via ws {payload}.")
        async_to_sync(channel_layer.group_send)(
            f"group_{self.feedback_ws_name}",
            {"type": "completed.data", "message": payload},
        )

    def _report_error_via_ws(self, error_description: str, log_url: str | None = None):
        if not self.feedback_ws_name:
            return
        channel_layer = get_channel_layer()
        payload = {
            "status": "failed",
            "text": error_description,
            "data": log_url,
        }
        logger.debug(f"Report errors via ws {payload}.")
        async_to_sync(channel_layer.group_send)(
            f"group_{self.feedback_ws_name}",
            {"type": "error.log", "message": payload},
        )

    def _get_logfile(self) -> tuple[core_models.File, str]:
        """
        Retrieve or create a logfile tied to the specified article and provide its download URL.

        :return: A tuple containing the logfile object and its corresponding download URL
        :rtype: tuple[core_models.File, str]
        :raises core_models.File.DoesNotExist: If the file retrieval or creation fails
        :raises django.urls.NoReverseMatch: If the URL reversal fails for the generated log URL
        """
        logfile, __ = core_models.File.objects.get_or_create(
            article_id=self.article_id,
            original_filename=get_feedback_logfile(self.feedback_uuid),
        )
        # We provide the log link after every conversion
        log_url = reverse(
            "download_single_file",
            kwargs={"file_id": logfile.id, "article_id": self.article_id},
        )
        return logfile, log_url

    def run(self):
        """
        Run the watermarking process for the manuscript file of an article.

        This method creates an in-memory INI file for configuration and retrieves the
        first manuscript file associated with the article. The file's content and name
        are passed to the YakuninClient to generate a watermarked version of the PDF
        file. Logs generated during the process are saved, and the resulting watermarked
        PDF is handled appropriately. Temporary directories used during the process
        are cleaned up to ensure no residual data remains.

        :raises YakuninRequestError: If unable to contact Yakunin or got a 500 error.

        :return: The generated watermarked PDF file.
        """
        tmpdir = None
        ini = self._create_in_memory_ini_file()
        filename, file_bytes, file_obj = self._get_source_file()
        client = YakuninClient(file=file_bytes, filename=filename)

        logfile, log_url = self._get_logfile()

        try:
            tmpdir, log = client.call_yakunin_watermark(ini_file=ini, feedback_ws_url=self.feedback_ws_url)
            # Always save the content of yakunin's log;
            # if the conversion was successful, it will not be shown to the user.
            logfile = self._save_yakunin_log(log, logfile)

            self._report_result_via_ws(
                result=logfile.description,
                text="Detailed conversion log available.",
                log_url=log_url,
            )

            return self._handle_generated_pdf(tmpdir, source_file=file_obj)

        except YakuninRequestError:
            logger.exception(f"Yakunin request error for article {self.article_id}.")
            from_email = get_setting("general", "support_email", self.article.journal).processed_value
            msg = _("The PDF file could not be generated. Please contact %s for assistance") % from_email
            self._report_error_via_ws(str(msg), log_url=log_url)

        finally:
            if tmpdir:
                shutil.rmtree(tmpdir)

    def _create_in_memory_ini_file(self) -> BytesIO:
        """
        Create an in-memory .ini file containing metadata related to the current article version.

        The method generates a `.ini` configuration file in memory that stores information concerning
        the article being processed, such as its journal code, unique identifier, version number, and
        positioning settings for a watermark. The generated `.ini` file is designed to be used
        internally to aid in the submission or revision-submission workflow. It handles both initial
        submission and revision-submission scenarios by considering whether the article already belongs
        to a review round.

        :return: A ``BytesIO`` object representing the generated .ini file with the appropriate content.
        :rtype: BytesIO
        """
        extra_config = getattr(settings, "YAKUNIN_CONFIG", "")
        # Warning: we can be called during submission, in which case there exists no version of the paper, so we are
        # generating the first PDF (v1) or we can be called during the revision-submission process. In the second case,
        # a version already exists, and we are working on the PDF for the next version (that will be created when the
        # revision-submission process is finished).
        version_number = self.article.current_review_round()
        if self.article.current_review_round_object():
            # if no review-round exists, Janeway forces current_review_round to "1" anyway, but it means that we are in
            # the first submission phase.
            version_number += 1
        ini_content = f"""
[wjs]
text = Not for distribution {self.article.journal.code} {self.article.id} v{version_number}
x = {settings.WATERMARK_X_POSITION}
y = {settings.WATERMARK_Y_POSITION}
{extra_config}
"""

        ini_file = BytesIO(ini_content.encode("utf-8"))
        ini_file.name = "wj.ini"
        ini_file.seek(0)
        return ini_file

    def _save_yakunin_log(self, log: str, logfile: core_models.File) -> core_models.File:
        """
        Save the Yakunin log content.

        Also set the logfile label (status) and description (result) based on the log content.
        This should work around any issues that might have happened with the WS feedback.

        :param log: The log content to save, provided as a string.
        :type log: str
        :param logfile: Logfile object to update.
        :type logfile: core_models.File
        :return: None
        """

        # NB: the logfile might have been changed (e.g. by the websocket feedbacks);
        # so we re-read it from the DB.
        logfile.refresh_from_db()

        path = logfile.self_article_path()
        Path(path).write_text(log, encoding="utf-8")

        # If we are here, it means that Yakunin has completed the process.
        logfile.label = "completed"

        # Now that the conversion is completed,
        # we can consider anything that is not "warning" or "error" as a success
        result = YakuninClient.conversion_result(log)
        if result != logfile.description:
            logfile.description = result
            logger.debug(f"Inconsistent result; was: {logfile.description}, set to: {result}.")

        logfile.save()
        return logfile

    def _handle_generated_pdf(self, tmpdir: Path, source_file: File) -> File | None:
        """
        Handle the generated PDF.

        Processes it, and save it appropriately.

        For new submissions, this method updates the article's file associations,
        placing the generated PDF in the correct slot
        while clearing and reassigning other related files.

        For revisions, this method updates the revision_storage data,
        clearing eventually replaced files.

        :param tmpdir: Temporary directory path containing the generated PDF
        :type tmpdir: Path
        :param source_file: The name of the source file. Used to generate the name of the PDF
        :type source_file: File
        :return: The converted file
        :rtype: File
        """
        generated_pdf_path = next(tmpdir.glob("*.pdf"), None)
        if not generated_pdf_path:
            logger.warning(f"No PDF file generated for {self.article_id}")
            return None

        # Let's call the PDF that we are storing in Janeway the same as the source file that originated it,
        # but with extension ".pdf"
        generated_pdf_filename = Path(source_file.original_filename).with_suffix(".pdf")
        if not self.is_revision:
            # We must remove eventual existing files before creating a new one, becase we are
            # re-using the same original filename.
            remove_existing_files_from_filesystem(self.article.pk, str(generated_pdf_filename))
            # HELP: can we simply remove the file currently associated to self.article.manuscript_files.first()?

        with generated_pdf_path.open("rb") as pdf_file:
            generated_pdf = File(pdf_file, name=str(generated_pdf_filename))
            generated_manuscript = core_files.save_file_to_article(
                file_to_handle=generated_pdf,
                article=self.article,
                owner=self.article.correspondence_author,
                label="Manuscript File",
            )

        # This is the wjs-submission revision which passes an explicit flag to ConvertManuscripToPdf
        # to use revision_storage
        if self.is_revision:
            # Remove eventual existing files.
            revision_storage = RevisionStorage.objects.get(article=self.article)
            if existing_manuscript_id := revision_storage.data["manuscript_files"]:
                core_models.File.objects.get(pk=existing_manuscript_id).delete()
                # Note that File.delete() also unlinks the file from the filesystem!

            # Do not remove here the source files: they have been already dealt with during upload

            # Store the new one in the revision_storage.
            # It will be moved to the article in step-8.
            revision_storage.data["manuscript_files"] = generated_manuscript.pk
            revision_storage.save()
        else:
            # TODO: check can be removed once we will port wjs-submission to production
            # this is where wjs-submission and legacy submission and revision ends
            use_wjs_submission = settings.WJS_USE_WJS_SUBMISSION.get(
                self.article.journal.code, settings.WJS_USE_WJS_SUBMISSION[None]
            )
            if not use_wjs_submission:
                # In case of legacy submission/revision, the author uploads a source-file into the
                # Article.manuscript_files (Janeway's default) we take it, generate the "real" manuscript and place
                # everything in its correct place.
                self.article.source_files.clear()
                self.article.source_files.set(self.article.manuscript_files.all())
            self.article.manuscript_files.clear()
            self.article.manuscript_files.add(generated_manuscript)
            self.article.save()

        # Do not remove the conversion log file just yet.
        # It can be deleted further down the review process,
        # e.g. when the editor takes any decision (accept/reject/ask-revision...).

        return generated_pdf

    def _get_source_file(self) -> tuple[str, str, core_models.File]:
        """
        Get the source file to convert.

        Take it directly from the article in case of normal submission.
        For revisions, use the RevisionStorage associated to the article.

        Return the file name and content.
        """
        file_obj = core_models.File.objects.get(pk=self.file_id)
        filename = file_obj.original_filename
        file_bytes = file_obj.get_file(self.article, as_bytes=True)
        return filename, file_bytes, file_obj


@dataclasses.dataclass
class BaseConvertLatexReport:
    report_text: str
    instance: WjsEditorAssignment
    logfile: core_models.File | None = None

    @property
    def yakunin_log_filename(self):
        raise NotImplementedError

    def _prepare_tex_file(self) -> bytes:
        """
        Prepare the LaTeX file content

        This method combines a preamble retrieved from a database and the report text, then appends the LaTeX document
        end command. The resulting content is encoded into UTF-8.

        :return: The complete LaTeX document as a UTF-8 encoded byte string.
        :rtype: bytes

        """
        preamble = LatexPreamble.objects.get(journal=self.instance.article.journal).report_preamble
        frozen_authors = self.instance.article.frozen_authors()
        authors = frozen_authors if frozen_authors.exists() else self.instance.article.authors.all()
        context = {
            "article": self.instance.article,
            "authors": authors,
        }
        preamble = HandleDownloadRevisionFiles.render_latexpreamble(preamble, context)
        full_text = f"{preamble}\n\n{self.report_text}\n\n" + r"\end{document}"
        return full_text.encode("utf-8")

    def _raise_for_yakunin_errors(self, log: str):
        """
        Check for Yakunin errors in the given log and raise exceptions if errors / warnings are found.

        :param log: Log content to be checked for Yakunin errors / warnings
        :type log: str
        :raises YakuninPDFGenerationError: If any Yakunin errors are found in the log
        :raises YakuninPDFGenerationWarnings: If any Yakunin warnings are found in the log
        """
        filtered_lines = report_yakunin_errors(log)
        if filtered_lines:
            raise YakuninPDFGenerationError("Error during conversion")
        filtered_lines = report_yakunin_warnings(log)
        if filtered_lines:
            raise YakuninPDFGenerationWarnings("Latex converted with warnings")

    def _save_yakunin_logs(self, log: str) -> core_models.File:
        """
        Saves Yakunin logs into a file and associates it with an article.

        This function takes a string representing the log content, converts it into
        a file object, and saves it as a log file associated with a specific article.
        The file is owned by the correspondence author of the article. It is labeled
        appropriately to indicate it contains conversion logs.

        :param log: The log content as a UTF-8 encoded string.
        :type log: str
        :return: A reference to the saved file.
        :rtype: core_models.File
        """
        remove_existing_files_from_filesystem(self.instance.article.pk, self.yakunin_log_filename)
        log_file = BytesIO(log.encode("utf-8"))
        log_content_file = File(log_file, name=self.yakunin_log_filename)

        return core_files.save_file_to_article(
            file_to_handle=log_content_file,
            article=self.instance.article,
            owner=self.owner,
            label=self._failed_conversion_log,
        )

    @property
    def owner(self):
        raise NotImplementedError

    def _handle_generated_file(self, unpack_dir: Path) -> File:
        """
        Handle the processing of a generated file in a specified directory.

        :param unpack_dir: Directory where the generated file is located
        :type unpack_dir: Path
        :return: Processed file object
        :rtype: File
        :raises NotImplementedError: If the method is not implemented
        """
        raise NotImplementedError

    def run(self):
        """
        Executes the process for generating, handling, and processing a LaTeX file using the Yakunin client service.

        The method prepares a temporary directory for the process, creates required files, and interacts with the
        Yakunin client to apply a watermark and handle the resulting files, ensuring clean up is performed after
        execution. Error handling should be extended for robustness.

        :return: Result of the `_handle_generated_file` method which processes the generated file.
        :rtype: Any
        """
        tmpdir = None
        tex_file = self._prepare_tex_file()
        client = YakuninClient(file=tex_file, filename=f"{self.report_filename}.tex")

        try:
            tmpdir, log = client.call_yakunin_mkpdf()
            self.logfile = self._save_yakunin_logs(log)
            self._raise_for_yakunin_errors(log)
            return self._handle_generated_file(tmpdir)
        except YakuninRequestError as e:
            from_email = get_setting("general", "support_email", self.instance.article.journal).processed_value
            msg = _("The PDF file could not be generated. Please contact %s for assistance") % from_email
            raise YakuninRequestError(msg) from e
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir)


@dataclasses.dataclass()
class ConvertEditorLatexReport(BaseConvertLatexReport):
    _failed_conversion_log = "Failed conversion log ConvertEditorLatexReport"
    _yakunin_log_filename_template = "conversion-EA_%s.log"
    _report_filename = "latex_editor_report_EA-%s"

    @property
    def yakunin_log_filename(self):
        return self._yakunin_log_filename_template % self.instance.pk

    @property
    def report_filename(self):
        return self._report_filename % self.instance.pk

    @property
    def owner(self):
        return self.instance.editor

    def _handle_generated_file(self, unpack_dir: Path) -> File:
        """
        Handles a generated PDF file within the specified unpack directory, processes it,
        and associates it with the relevant article.

        :param unpack_dir: A directory containing the unpacked files including the
            generated PDF.
        :type unpack_dir: Path
        :return: The processed PDF file saved and linked to the article.
        :rtype: File
        """
        generated_pdf_path = next(unpack_dir.glob("*.pdf"), None)
        generated_pdf_filename = f"{self._report_filename}.pdf"
        remove_existing_files_from_filesystem(self.instance.article.pk, generated_pdf_filename)
        with generated_pdf_path.open("rb") as pdf_file:
            generated_pdf = File(pdf_file, name=generated_pdf_filename)
            generated_pdf_jf = core_files.save_file_to_article(
                file_to_handle=generated_pdf,
                article=self.instance.article,
                owner=self.owner,
                label="Editor report PDF",
            )
        self.instance.editor_report_pdf_draft = generated_pdf_jf
        self.instance.save()
        return generated_pdf_jf


@dataclasses.dataclass
class ConvertReviewerLatexReport(BaseConvertLatexReport):
    _failed_conversion_log = "Failed conversion log ConvertReviewerLatexReport"
    _yakunin_log_filename_template = "conversion-RA_%s.log"
    _report_filename = "latex_editor_report_RA-%s"

    @property
    def yakunin_log_filename(self):
        return self._yakunin_log_filename_template % self.instance.pk

    @property
    def report_filename(self):
        return self._report_filename % self.instance.pk

    @property
    def owner(self):
        return self.instance.reviewer

    def _handle_generated_file(self, unpack_dir: Path) -> File:
        """
        Handles a generated PDF file within the specified unpack directory, processes it,
        and associates it with the relevant article.

        :param unpack_dir: A directory containing the unpacked files including the
            generated PDF.
        :type unpack_dir: Path
        :return: The processed PDF file saved and linked to the article.
        :rtype: File
        """
        generated_pdf_path = next(unpack_dir.glob("*.pdf"), None)
        generated_pdf_filename = f"{self._report_filename}.pdf"
        remove_existing_files_from_filesystem(self.instance.article.pk, generated_pdf_filename)
        with generated_pdf_path.open("rb") as pdf_file:
            generated_pdf = File(pdf_file, name=generated_pdf_filename)
            generated_pdf_jf = core_files.save_file_to_article(
                file_to_handle=generated_pdf,
                article=self.instance.article,
                owner=self.owner,
                label="Reviewer report PDF",
            )
        self.instance.tex_report_pdf = generated_pdf_jf
        self.instance.save()
        return generated_pdf_jf


@dataclasses.dataclass
class AccessModeSpecialRequestNotification:
    # TODO specs#2157: convert to submission-check and create attention condition
    submission_data: ArticleSubmission

    def _check_conditions(self):
        return self.submission_data.special_request

    def _send_notification(self):
        """Log a message to the EO containing information about a special request related to access-mode."""
        from utils.management.commands.test_fire_event import create_fake_request

        fake_request = create_fake_request(user=None, journal=self.submission_data.article.journal)
        context = {
            "article": self.submission_data.article,
            "submission_data": self.submission_data,
        }
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="access_mode_special_request_notification_subject",
            journal=self.submission_data.article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="access_mode_special_request_notification_body",
            journal=self.submission_data.article.journal,
            request=fake_request,
            context=context,
            template_is_setting=True,
        )
        communication_utils.log_operation(
            article=self.submission_data.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.submission_data.article.correspondence_author,
            recipients=[get_eo_user(self.submission_data.article)],
        )

    def run(self):
        if self._check_conditions():
            self._send_notification()
