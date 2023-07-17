import dataclasses
from typing import Any, Dict

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest
from django_fsm import has_transition_perm
from review.logic import quick_assign
from review.models import ReviewAssignment

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
        return reviewer not in workflow.article_authors

    def _check_conditions(self) -> bool:
        reviewer_conditions = self.check_reviewer_conditions(self.workflow, self.reviewer)
        editor_permissions = has_transition_perm(self.workflow.assign_referee, self.editor)
        return reviewer_conditions and editor_permissions

    def _assign_reviewer(self) -> ReviewAssignment:
        return quick_assign(request=self.request, article=self.workflow.article, reviewer_user=self.reviewer)

    def _notify_reviewer(self):
        # TODO: Send email notification
        print("SEND EMAIL")

    def _update_state(self):
        self.workflow.assign_referee()
        self.workflow.save()

    def run(self) -> ReviewAssignment:
        with transaction.atomic():
            self._check_conditions()
            assignment = self._assign_reviewer()
            if not assignment:
                raise ValueError("Invalid state transition")
            self._notify_reviewer()
            self._update_state()
        return assignment
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
