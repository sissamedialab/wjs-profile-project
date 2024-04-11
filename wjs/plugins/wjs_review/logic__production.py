"""Logic classes for production-related actions & co.

This module should be *-imported into logic.py
"""
import dataclasses
import datetime
from io import BytesIO
from typing import Any, Dict, Optional
from zipfile import ZipFile

from core.files import save_file_to_article
from core.models import File
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.module_loading import import_string
from django_fsm import can_proceed
from events import logic as events_logic
from plugins.typesetting.models import (
    GalleyProofing,
    TypesettingAssignment,
    TypesettingRound,
)
from submission.models import STAGE_PROOFING, STAGE_TYPESETTING, Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile.utils import render_template_from_setting

from . import communication_utils
from .models import ArticleWorkflow, Message
from .permissions import has_typesetter_role_by_article

logger = get_logger(__name__)
Account = get_user_model()


@dataclasses.dataclass
class VerifyProductionRequirements:
    """The system (generally), verifies that the article is ready for tyepsetter."""

    articleworkflow: ArticleWorkflow

    def _check_conditions(self) -> bool:
        # TODO: do we have any other conditions to check?
        return self._perform_checks()

    def _perform_checks(self) -> bool:
        """Apply functions that verify if an accepted article is ready for typs."""
        journal = self.articleworkflow.article.journal.code
        checks_functions = settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS.get(
            journal,
            settings.WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS.get(None, []),
        )
        # TODO: how do we report issues?
        for check_function in checks_functions:
            if not import_string(check_function)(self.articleworkflow.article):
                return False
        return True

    def _log_acceptance_issues(self):
        """Log that something prevented an accepted article to be ready for tyepsetters."""
        message_subject = (
            f"Issues after acceptance - article {self.articleworkflow.article.pk} not ready for typesetters."
        )
        message_body = f"""Some issues prevented {self.articleworkflow} from being set ready for typesetter.

        Please check {reverse_lazy("wjs_article_details", kwargs={"pk": self.articleworkflow.article.pk})}

        """

        message = communication_utils.log_operation(
            article=self.articleworkflow.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[
                communication_utils.get_eo_user(self.articleworkflow.article),
            ],
            message_type=Message.MessageTypes.SYSTEM,
        )
        return message

    def run(self) -> ArticleWorkflow:
        with transaction.atomic():
            if not self._check_conditions():
                # Here we do not raise an exception, because doing so would prevent an editor from accepting an
                # article. Instead we send a message to EO.
                self._log_acceptance_issues()
            else:
                self.articleworkflow.system_verifies_production_requirements()
                self.articleworkflow.save()
            return self.articleworkflow


# https://gitlab.sissamedialab.it/wjs/specs/-/issues/667
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
        if not article.typesettinground_set.exists():
            return True

        pending_assignments = article.typesettinground_set.filter(
            typesettingassignment__completed__isnull=True,
            typesettingassignment__cancelled__isnull=True,
        ).exists()
        return not pending_assignments

    def _check_conditions(self) -> bool:
        """Check if the conditions for the assignment are met."""
        if self.request.user is None:
            state_conditions = can_proceed(self.article.articleworkflow.system_assigns_typesetter)
        elif self.is_user_typesetter():
            state_conditions = can_proceed(self.article.articleworkflow.typesetter_takes_in_charge)
        else:
            state_conditions = can_proceed(self.article.articleworkflow.typesetter_takes_in_charge)
            logger.error(
                f"Unexpected user {self.request.user}"
                f" attempting to assign typesetter {self.typesetter}"
                f" onto article {self.article.pk}."
                " Checking anyway...",
            )

        typesetter_is_typesetter = has_typesetter_role_by_article(self.article.articleworkflow, self.typesetter)
        article_conditions = self.check_article_conditions(self.article)
        return state_conditions and typesetter_is_typesetter and article_conditions

    def _create_typesetting_round(self):
        self.article.stage = STAGE_TYPESETTING
        self.article.save()
        typesetting_round, _ = TypesettingRound.objects.get_or_create(
            article=self.article,
        )
        return typesetting_round

    def _update_state(self):
        """Run FSM transition."""
        if self.request.user is None:
            self.article.articleworkflow.system_assigns_typesetter()
        elif self.is_user_typesetter():
            self.article.articleworkflow.typesetter_takes_in_charge()
        else:
            self.article.articleworkflow.typesetter_takes_in_charge()
            logger.error(
                f"Unexpected user {self.request.user}"
                f" assigning typesetter {self.typesetter}"
                f" onto article {self.article.pk}."
                " Proceeding anyway...",
            )
        self.article.articleworkflow.save()

    def _assign_typesetter(self) -> TypesettingAssignment:
        assignment = TypesettingAssignment.objects.create(
            round=self._create_typesetting_round(),
            typesetter=self.typesetter,
            # at the moment we assume that the typesetter automatically accepts the assignment
            # both when he takes in charge (naturally), but also when the system assigns him
            accepted=timezone.now(),
            due=timezone.now() + timezone.timedelta(days=settings.TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS),
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
            setting_group_name="wjs_review",
            setting_name="typesetting_assignment_subject",
            journal=self.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
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


@dataclasses.dataclass
class RequestProofs:
    """The typesetter completes a typesetting round and requires proofreading from the author."""

    # Roughly equivalent Janeway's "Typesetting task completed"
    # (do not confuse with "typesetting complete", that moves the article to pre-publication)

    workflow: ArticleWorkflow
    request: HttpRequest
    assignment: TypesettingAssignment
    typesetter: Account

    def _check_conditions(self):
        """Check if the conditions for the assignment are met."""
        self.article = self.workflow.article
        typesetter_is_typesetter = has_typesetter_role_by_article(self.workflow, self.typesetter)
        state_conditions = can_proceed(self.workflow.typesetter_submits)
        # TODO: Write a condition for Galleys.
        return typesetter_is_typesetter and state_conditions

    def _update_state(self):
        """Run FSM transition."""
        self.workflow.typesetter_submits()
        self.workflow.save()
        self.article.stage = STAGE_PROOFING
        self.article.save()

    def _create_proofing_assignment(self):
        self.proofreader = self.article.correspondence_author
        if self.assignment.round.round_number == 1:
            due = timezone.now().date() + datetime.timedelta(
                days=settings.PROOFING_ASSIGNMENT_MAX_DUE_DAYS,
            )
        else:
            due = timezone.now().date() + datetime.timedelta(
                days=settings.PROOFING_ASSIGNMENT_MIN_DUE_DAYS,
            )
        proofing_assignment = GalleyProofing.objects.create(
            round=self.assignment.round,
            proofreader=self.proofreader,
            accepted=timezone.now(),
            due=due,
            manager=self.typesetter,
        )
        return proofing_assignment

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "article": self.article,
            "author": self.proofreader,
        }

    def _log_operation(self, context) -> Message:
        """Log the operation."""
        message_subject = get_setting(
            setting_group_name="wjs_review",
            setting_name="proofreading_request_subject",
            journal=self.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="proofreading_request_body",
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
                self.proofreader,
            ],
            message_type=Message.MessageTypes.SYSTEM,
        )
        return message

    #   - with multi-template message? (see US ID:NA row:260 order:235)
    #     - similar to editor-selects-reviewer but with more template messages to choose from

    def run(self) -> GalleyProofing:
        """Move the article state to PROOFREADING and notify the author."""
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self._update_state()
            proofing_assignment = self._create_proofing_assignment()
            self._log_operation(context=self._get_message_context())
            return proofing_assignment


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


@dataclasses.dataclass
class PublishArticle:
    """Manage an article's publication."""

    # Placeholder!

    workflow: ArticleWorkflow
    request: HttpRequest

    def _trigger_workflow_event(self):
        # TODO: review me!
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""
        workflow_kwargs = {
            "handshake_url": "wjs_review_list",
            "request": self.request,
            "article": self.workflow.article,
            "switch_stage": True,
        }
        self._trigger_article_event(events_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE, workflow_kwargs)

    def _trigger_article_event(self, event: str, context: Dict[str, Any]):
        # TODO: refactor with Handledecision._trigger_article_event
        """Trigger the given event."""
        return events_logic.Events.raise_event(event, task_object=self.workflow.article, **context)

    def run(self):
        # TODO: writeme!
        self._trigger_workflow_event()


@dataclasses.dataclass
class UploadFile:
    """Allow the typesetter to upload typesetting files."""

    typesetter: Account
    request: HttpRequest
    assignment: TypesettingAssignment
    file_to_upload: File

    def _remove_file_from_assignment(self):
        """Empties the files_to_typeset field of TypesettingAssignment."""
        self.assignment.files_to_typeset.clear()

    def _delete_core_files_record(self):
        file_record = self.assignment.files_to_typeset.get()
        file_record.delete()

    def _update_typesetting_assignment(self, uploaded_file):
        """Create the relation in files_to_typeset field of TypesettingAssignment"""
        self.assignment.files_to_typeset.add(uploaded_file)

    def run(self):
        """Main method to execute the file upload logic."""
        with transaction.atomic():
            # Check if there are any files already associated
            if self.assignment.files_to_typeset.exists():
                self._delete_core_files_record()
                self._remove_file_from_assignment()

            uploaded_file = save_file_to_article(self.file_to_upload, self.assignment.round.article, self.typesetter)
            self._update_typesetting_assignment(uploaded_file)


@dataclasses.dataclass
class HandleDownloadRevisionFiles:
    """Handle download of revision files."""

    workflow: ArticleWorkflow
    request: HttpRequest

    def _gather_files(self):
        """Gather all files to download."""
        self.workflow.rename_manuscript_files()
        self.workflow.rename_source_files()
        manuscript_files = list(self.workflow.article.manuscript_files.all())
        data_figure_files = list(self.workflow.article.data_figure_files.all())
        supplementary_files = [supp.file for supp in self.workflow.article.supplementary_files.all()]
        source_files = list(self.workflow.article.source_files.all())

        all_files = manuscript_files + data_figure_files + supplementary_files + source_files
        return all_files

    def _create_archive(self, files):
        """Create a ZIP archive from the given files."""
        in_memory = BytesIO()
        with ZipFile(in_memory, "w") as archive:
            for file in files:
                file_path = file.self_article_path()
                archive.write(file_path, arcname=file.original_filename)

        in_memory.seek(0)
        return in_memory

    def run(self):
        """Serve the archive for download."""
        files = self._gather_files()
        archive = self._create_archive(files)

        return archive.getvalue()
