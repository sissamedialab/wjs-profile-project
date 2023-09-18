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
from events import logic as event_logic
from review.logic import assign_editor, quick_assign
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from review.views import (
    accept_review_request,
    decline_review_request,
    upload_review_file,
)
from submission.models import STAGE_ASSIGNED, Article

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

from . import permissions

if TYPE_CHECKING:
    from .forms import ReportForm

from .models import ArticleWorkflow, EditorDecision

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

    def run(self) -> ArticleWorkflow:
        with transaction.atomic():
            self._create_workflow()
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self._assign_editor()
            self._update_state()
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

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        reviewer_conditions = self.check_reviewer_conditions(self.workflow, self.reviewer)
        editor_conditions = self.check_editor_conditions(self.workflow, self.editor)
        return reviewer_conditions and editor_conditions

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

    def _assign_reviewer(self) -> ReviewAssignment:
        """
        Assign the reviewer to the article.

        Use janeway review logic quick_assign function.
        """
        return quick_assign(request=self.request, article=self.workflow.article, reviewer_user=self.reviewer)

    def _notify_reviewer(self):
        # TODO: Send email notification
        print("SEND EMAIL")

    def run(self) -> ReviewAssignment:
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
            self._notify_reviewer()
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

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        reviewer_conditions = self.check_reviewer_conditions(self.assignment, self.reviewer)
        editor_conditions = self.check_editor_conditions(self.assignment, self.editor)
        return reviewer_conditions and editor_conditions

    def _handle_accept(self) -> Optional[bool]:
        """
        Accept the review by calling janeway :py:func:`accept_review_request`.

        Response returned by janeway is discarded.

        Return boolean value of the assignment date_accepted field.
        """
        accept_review_request(request=self.request, assignment_id=self.assignment.pk)
        self.assignment.refresh_from_db()
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
        if self.assignment.date_declined:
            return False

    def _activate_invitation(self, token: str):
        user = JCOMProfile.objects.get(invitation_token=token)
        user.is_active = True
        user.gdpr_checkbox = True
        user.save()

    def _save_date_due(self):
        date_due = self.form_data.get("date_due")
        if date_due:
            self.assignment.save()

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


# Some states with their actions
# TBD: do we want to keep something of this sort here or in logic.py?
class ED_TO_BE_SE:  # noqa N801 CapWords convention
    actions = ("dir - selects editor",)


class EDITO_SELEC:  # noqa N801 CapWords convention
    actions = (
        "ed - declines assignment",
        "ed - assigns different editor",
        "ed - accepts",
        "ed - rejects",
        "ed - deems not suitable",
        "ed - request revision",
        "ed - assigns self as reviewer",
        "ed - assigns reviewer",
        "ed - removes reviewer",
        "ed - reminds reviewer assignment",
        "ed - reminds reviewer report",
        "ed - postpones rev.report deadline",
        "ed - ask report revision",
        "rev - accept",
        "rev - decline",
        "rev - write report",
        "rev - postpones rev.report deadline",
        "dir - reminds editor",
    )


class _TO_BE_REV_:  # noqa N801 CapWords convention
    actions = (
        "ed - reminds author",
        "au - submits new version",
        "au - confirms previous manuscript",
    )


class _REJECTED__:  # noqa N801 CapWords convention
    actions = ("admin - opens appeal",)


class PA_MI_HA_IS:  # noqa N801 CapWords convention
    actions = (
        "admin - requires resubmission",
        "admin - deems not suitable",
        "admin - deems issue unimportant",
    )


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

    def check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        has_journal = self.request.journal
        return has_journal

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

    def _assign_reviewer(self, user: JCOMProfile) -> ReviewAssignment:
        """Create a review assignment for the invited user."""
        assign_service = AssignToReviewer(
            reviewer=user.janeway_account,
            workflow=self.workflow,
            editor=self.editor,
            form_data={},
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
            event_logic.Events.raise_event(
                event_logic.Events.ON_REVIEW_COMPLETE,
                task_object=assignment.article,
                **kwargs,
            )

    def run(self):
        with transaction.atomic():
            assignment = self._upload_files(self.assignment, self.request)
            assignment = self._save_report_form(assignment, self.form)
            assignment = self._complete_review(assignment, self.submit_final)
            self._trigger_complete_event(assignment, self.request, self.submit_final)
            return assignment


@dataclasses.dataclass
class HandleDecision:
    workflow: ArticleWorkflow
    form_data: Dict[str, Any]
    user: Account
    request: HttpRequest

    def check_conditions(self) -> bool:
        """Check if the conditions for the decision are met."""
        editor_selected = self.workflow.state == ArticleWorkflow.ReviewStates.EDITOR_SELECTED
        editor_has_permissions = permissions.is_article_editor(self.workflow, self.user)
        return editor_selected and editor_has_permissions

    def _trigger_article_event(self, event: str, context: Dict[str, Any]):
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""

        return event_logic.Events.raise_event(event, task_object=self.workflow.article, **context)

    def _trigger_workflow_event(self):
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""
        workflow_kwargs = {
            "handshake_url": "wjs_review_list",
            "request": self.request,
            "article": self.workflow.article,
            "switch_stage": True,
        }
        self._trigger_article_event(event_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE, workflow_kwargs)

    @staticmethod
    def _get_email_context(article: Article, request: HttpRequest, form_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "article": article,
            "request": request,
            "decision": form_data["decision"],
            "user_message_content": form_data["decision_editor_report"],
            "skip": False,
        }

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
        self._trigger_article_event(event_logic.Events.ON_ARTICLE_ACCEPTED, context)
        return self.workflow.article

    def _decline_article(self) -> Article:
        """
        Decline article.

        - Call janeway decline_article
        - Advance workflow state
        - Trigger ON_ARTICLE_DECLINED event
        """
        self.workflow.article.decline_article()

        self.workflow.editor_writes_editor_report()
        self.workflow.editor_rejects_paper()
        self.workflow.save()

        context = HandleDecision._get_email_context(self.workflow.article, self.request, self.form_data)
        self._trigger_article_event(event_logic.Events.ON_ARTICLE_DECLINED, context)
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
        self._trigger_article_event(event_logic.Events.ON_ARTICLE_DECLINED, context)
        return self.workflow.article

    def _close_unsubmitted_reviews(self):
        """
        Mark all non completed reviews are as declined and closed.
        """
        for assignment in self.workflow.article.reviewassignment_set.filter(is_complete=False):
            # FIXME: Is this the righe state?
            assignment.date_declined = timezone.now()
            assignment.is_complete = True
            assignment.save()
            # TODO: Should we email reviewers to inform them that the review is closed?

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
            if self.form_data["decision"] == ArticleWorkflow.Decisions.ACCEPT:
                self._accept_article()
            elif self.form_data["decision"] == ArticleWorkflow.Decisions.REJECT:
                self._decline_article()
            elif self.form_data["decision"] == ArticleWorkflow.Decisions.NOT_SUITABLE:
                self._not_suitable_article()
            self._trigger_workflow_event()
            if self.form_data["decision"]:
                self._close_unsubmitted_reviews()
            return decision
