import dataclasses
from typing import Any, Dict

from core.models import AccountRole, Role
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from django_fsm import has_transition_perm
from review.logic import quick_assign
from review.models import EditorAssignment, ReviewAssignment

from .models import ArticleWorkflow

Account = get_user_model()


@dataclasses.dataclass
class AssignToReviewer:
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
        editor_permissions = has_transition_perm(self.workflow.assign_referee, self.editor)
        return reviewer_conditions and editor_permissions and editor_conditions

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

    def _update_state(self):
        """Run FSM transition."""
        self.workflow.assign_referee()
        self.workflow.save()

    def run(self) -> ReviewAssignment:
        # TODO: verificare in futuro se controllare assegnazione multiupla allo stesso reviewer quando si saranno
        #       decisi i meccanismi digestione dei round e delle versioni
        # TODO: se il reviewer non ha il ruolo bisogna fare l'enrolment
        # - controllare che
        #   - il reviewer possa essere assegnato
        #   - lo stato sia compatibile con assign_referee
        # - assegna il reviewer
        # - invia la mail
        # - aggiorna lo stato
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
            self._update_state()
        return assignment
