import dataclasses
from typing import Any, Dict, Optional

from core.models import AccountRole, Role
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from django_fsm import can_proceed
from review.logic import assign_editor, quick_assign
from review.models import EditorAssignment, ReviewAssignment, ReviewRound
from review.views import accept_review_request, decline_review_request
from submission.models import STAGE_ASSIGNED, Article

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

from .models import ArticleWorkflow

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

    def _check_revert_state(self) -> bool:
        """
        Check if the state of the article needs to be reverted (eg: no more pending reviews exists).
        """
        return not self.assignment.article.reviewassignment_set.filter(
            is_complete=False,
            review_round=self.assignment.review_round,
        ).exists()

    def _revert_state(self):
        """
        Revert the state of the article to the previous state.
        """
        self.assignment.article.articleworkflow.deassign_referee()
        self.assignment.article.articleworkflow.save()

    def _handle_decline(self) -> Optional[bool]:
        """
        Decline the review by calling janeway :py:func:`decline_review_request`.

        Response returned by janeway is discarded.

        Return boolean value of the assignment date_declined field.
        """
        decline_review_request(request=self.request, assignment_id=self.assignment.pk)
        self.assignment.refresh_from_db()
        if self.assignment.date_declined:
            if self._check_revert_state():
                self._revert_state()
            return False

    def _activate_invitation(self, token: str):
        user = JCOMProfile.objects.get(invitation_token=token)
        user.is_active = True
        user.gdpr_checkbox = True
        user.save()

    def run(self) -> Optional[bool]:
        with transaction.atomic():
            conditions = self.check_conditions()
            if not conditions:
                raise ValidationError(_("Transition conditions not met"))
            if self.token:
                self._activate_invitation(self.token)
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
        user_exists = JCOMProfile.objects.filter(email=self.form_data["email"]).exists()
        has_journal = self.request.journal
        return not user_exists and has_journal

    def _create_user(self, token: str) -> JCOMProfile:
        user = JCOMProfile.objects.create(
            email=self.form_data["email"],
            first_name=self.form_data["first_name"],
            last_name=self.form_data["last_name"],
            is_active=False,
            invitation_token=token,
        )
        return user

    def _notify_user(self):
        """Notify current user that the invitation has been sent."""
        messages.add_message(self.request, messages.INFO, _("Invitation sent to %s.") % self.form_data["last_name"])

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
            token = self._generate_token()
            user = self._create_user(token)
            self._assign_reviewer(user)
            self._notify_user()
            return user
