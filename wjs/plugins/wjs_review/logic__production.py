"""Logic classes for production-related actions & co.

This module should be *-imported into logic.py
"""

import dataclasses
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django_fsm import can_proceed
from plugins.typesetting.models import (
    GalleyProofing,
    TypesettingAssignment,
    TypesettingRound,
)
from submission.models import STAGE_TYPESETTING, Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import render_template_from_setting

from . import communication_utils
from .models import Message
from .permissions import is_typesetter

logger = get_logger(__name__)
Account = get_user_model()


@dataclasses.dataclass
class AssignTypesetter:
    """Assign a typesetter to an article.

    This can be used either when
    - typesetter takes a paper in charge
    - system automagically assigns a typesetter
    """

    article: Article
    typesetter: Account
    request: HttpRequest
    assignment: Optional[TypesettingAssignment] = None

    def is_user_typesetter(self) -> bool:
        return self.request.user == self.typesetter

    @staticmethod
    def check_article_conditions(article: Article) -> bool:
        """Check that the article has no pending typesetting assignments."""
        pending_assignments = article.typesettingassignment_set.filter(
            completed__isnull=True,
            cancelled__isnull=True,
        ).exists()
        return not pending_assignments

    def _check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        if self.request.user is None:
            state_conditions = can_proceed(self.workflow.system_assigns_typesetter)
        elif self.is_user_typesetter():
            state_conditions = can_proceed(self.workflow.typesetter_takes_in_charge)

        typesetter_is_typesetter = is_typesetter(self.article, self.typesetter)
        article_conditions = self.check_article_conditions(self.article)
        return state_conditions and typesetter_is_typesetter and article_conditions

    def _create_typesetting_round(self):
        self.article.stage = STAGE_TYPESETTING
        self.article.save()
        typesetting_round = TypesettingRound.objects.get_or_create(
            article=self.article,
        )
        return typesetting_round

    def _update_state(self):
        """Run FSM transition."""
        if self.request.user is None:
            self.workflow.system_assigns_typesetter()
        elif self.is_user_typesetter():
            self.workflow.typesetter_takes_in_charge()
        self.workflow.save()

    def _assign_typesetter(self) -> TypesettingAssignment:
        assignment = TypesettingAssignment.objects.create(
            round=self._create_typesetting_round(),
            typesetter=self.typesetter,
            # at the moment we assume that the typesetter automatically accepts the assignment
            # both when he takes in charge (naturally), but also when the system assigns him
            accepted=timezone.now,
            due=timezone.now + timezone.timedelta(days=settings.TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS),
        )
        return assignment

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "article": self.article,
            "typesetter": self.typesetter,
        }

    def _log_operation(self, context) -> Message:
        """Log the operation."""
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="typesetting_assignment_subject",
            journal=self.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="typesetting_assignment_body",
            journal=self.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message = communication_utils.log_operation(
            article=self.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[
                self.typesetter,
            ],
            message_type=Message.MessageTypes.SYSTEM,
        )
        return message

    def _mark_message_read(self, message: Message):
        message.messagerecipients_set.filter(recipient=self.typesetter).update(read=True)
        message.save()

    def run(self) -> TypesettingAssignment:
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self.assignment = self._assign_typesetter()
            self._update_state()
            context = self._get_message_context()
            message = self._log_operation(context=context)
            if self.is_user_typesetter():
                self._mark_message_read(message)
            return self.assignment
        #  - TBD: create production.models.TypesettingTask
        #  - ✗ TBD: create TypesettingClaim
        #  - ✗ TBD: create TypesettingAssignment.corrections


# https://gitlab.sissamedialab.it/wjs/specs/-/issues/671 (drop this comment)
@dataclasses.dataclass
class RequestProofs:
    """The typesetter completes a typesetting round and requires proofreading from the author."""

    # Roughly equivalent Janeway's "Typesetting task completed"
    # (do not confuse with "typesetting complete", that moves the article to pre-publication)

    typesetting_assignment: TypesettingAssignment
    request: HttpRequest  # ???
    assignment: Optional[TypesettingAssignment] = None

    def run(self) -> GalleyProofing:
        """Move the article state to PROOFREADING and notify the author."""
        pass

    # _check_conditions
    #   - ...
    #   - galleys are present (? TBV: AFAICT an article's galleys are
    #                          all typeset_files from all typesetting assignments)
    #   - ...

    # _create_proofing_assignment
    #   - prooreader = article.correspondence_author
    #   - with due date (different defaults if typ-round==1 or typ-rount>1)
    #   - with multi-template message? (see US ID:NA row:260 order:235)
    #     - similar to editor-selects-reviewer but with more template messages to choose from


@dataclasses.dataclass
class SendProofs:
    # TBD: typetting plugin has a maybe-similar concept of "corrections"
    """The author sends a request for corrections (or a can-be-published green light).

    E.g.:
    - a text with some notes ("in pg.X l.Y, change A with B")
    - a file with some notes
    - (future) a patch-like something generated by on-premises Overleaf
    - ...
    """

    article: Article
    assignment: TypesettingAssignment  # OR GalleyProofing
    request: HttpRequest  # ???

    def run(self) -> GalleyProofing:
        # OR: -> proofing.ProofingAssignment
        # But! I think typesetting plugins obsoletes proofing
        pass

    # TBD: _bump_round of same typesetting assignment or create a new one?
    #   - probably best place to bump the round number is typesetter-side:
    #     - when typ takes in charge -> round 1
    #     - when proofs are in, typ can ask for another round
    # The choosen solution must allow us to keep track of changes that the typ makes to the typeset files in each round
    # (similar to versioning the files)
