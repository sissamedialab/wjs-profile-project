"""Logic classes for production-related actions & co.

This module should be *-imported into logic.py
"""

import dataclasses
import datetime
import os
import shutil
import tarfile
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple
from zipfile import ZipFile

import lxml.html
import requests
from core.files import save_file_to_article
from core.models import File as JanewayFile
from core.models import Galley, SupplementaryFile
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.module_loading import import_string
from django_fsm import can_proceed
from django_q.tasks import async_task
from events import logic as events_logic
from lxml.html import HtmlElement
from plugins.typesetting.models import (
    GalleyProofing,
    TypesettingAssignment,
    TypesettingRound,
)
from production.logic import save_galley, save_galley_image
from submission.models import (
    STAGE_PROOFING,
    STAGE_READY_FOR_PUBLICATION,
    STAGE_TYPESETTING,
    Article,
)
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile import import_utils
from wjs.jcom_profile.import_utils import (
    decide_galley_label,
    evince_language_from_filename_and_article,
    process_body,
)
from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.utils import (
    create_rich_fake_request,
    render_template,
    render_template_from_setting,
)

from . import communication_utils
from .models import ArticleWorkflow, LatexPreamble, Message
from .permissions import (
    has_typesetter_role_by_article,
    is_article_author,
    is_article_typesetter,
)
from .utils import (
    get_tex_source_file_from_archive,
    guess_typesetted_texfile_name,
    tex_file_has_queries,
)

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
            verbosity=Message.MessageVerbosity.FULL,
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
            recipients=[
                self.typesetter,
            ],
            verbosity=Message.MessageVerbosity.FULL,
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )
        return message

    def _mark_message_read(self, message: Message):
        message.messagerecipients_set.filter(recipient=self.typesetter).update(read=True)
        message.save()

    def save_supplementary_files_at_acceptance(self):
        """We have an archival model in ArticleWorkflow to save supplementary files at Typesetter acceptance."""
        self.article.articleworkflow.supplementary_files_at_acceptance.set(self.article.supplementary_files.all())

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
            self.save_supplementary_files_at_acceptance()
            return self.assignment


@dataclasses.dataclass
class RequestProofs:
    """The typesetter completes a typesetting round and requires proofreading from the author."""

    # Roughly equivalent Janeway's "Typesetting task completed"
    # (do not confuse with "typesetting complete", that moves the article to pre-publication)

    workflow: ArticleWorkflow
    request: HttpRequest
    assignment: TypesettingAssignment
    typesetter: Account
    article: Article = dataclasses.field(init=False)  # just a shortcut

    def __post_init__(self):
        """Find the source files."""
        self.article = self.workflow.article

    def _check_conditions(self) -> Tuple[bool, Optional[str]]:
        """Check if the conditions for the assignment are met."""
        if not has_typesetter_role_by_article(self.workflow, self.typesetter):
            return (False, "User attempting action is not the paper's typesetter")
        if not can_proceed(self.workflow.typesetter_submits):
            return (False, "Invalid transition")
        # Not enforcing any check on galleys in order to permit typ to ask proofs in any condition. This allows, for
        # instance, to request proofs right away (before the typ does any work), to meet author's request to upload a
        # "corrected" version (sometimes non-English authors have their paper checked for English by professionals, but
        # only after acceptance).
        return (True, None)

    def _update_state(self):
        """Run FSM transition."""
        self.workflow.typesetter_submits()
        self.workflow.save()
        self.article.stage = STAGE_PROOFING
        self.article.save()
        self.assignment.completed = timezone.now()
        self.assignment.save()

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
        proofing_assignment.proofed_files.set(self.assignment.galleys_created.all())
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
            recipients=[
                self.proofreader,
            ],
            verbosity=Message.MessageVerbosity.FULL,
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )
        return message

    #   - with multi-template message? (see US ID:NA row:260 order:235)
    #     - similar to editor-selects-reviewer but with more template messages to choose from

    def run(self) -> GalleyProofing:
        """Move the article state to PROOFREADING and notify the author."""
        with transaction.atomic():
            green_light, reason = self._check_conditions()
            if not green_light:
                raise ValueError(reason)
            self._update_state()
            proofing_assignment = self._create_proofing_assignment()
            self._log_operation(context=self._get_message_context())
            return proofing_assignment


@dataclasses.dataclass
class UploadFile:
    """Allow the typesetter to upload typesetted files."""

    typesetter: Account
    request: HttpRequest
    assignment: TypesettingAssignment
    file_to_upload: File

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.assignment.round.article.articleworkflow, self.request.user)

    def _check_file_condition(self):
        return self.file_to_upload and self.file_to_upload.content_type in ["application/zip"]

    def _remove_file_from_assignment(self):
        """Empties the files_to_typeset field of TypesettingAssignment."""
        self.assignment.files_to_typeset.clear()

    def _delete_core_files_record(self):
        file_record = self.assignment.files_to_typeset.get()
        file_record.delete()

    def _update_typesetting_assignment(self, uploaded_file):
        """Create the relation in files_to_typeset field of TypesettingAssignment"""
        self.assignment.files_to_typeset.add(uploaded_file)
        self.assignment.round.article.articleworkflow.production_flag_galleys_ok = (
            ArticleWorkflow.GalleysStatus.NOT_TESTED
        )
        self.assignment.round.article.articleworkflow.save()

    def _look_for_queries_in_archive(self):
        """Check if there are any queries in the archive's source tex file."""
        filename = guess_typesetted_texfile_name(self.assignment.round.article)
        tex_file = get_tex_source_file_from_archive(self.file_to_upload, filename)
        self.assignment.round.article.articleworkflow.production_flag_no_queries = not tex_file_has_queries(tex_file)
        self.assignment.round.article.articleworkflow.save()

    def run(self):
        """Main method to execute the file upload logic."""
        with transaction.atomic():
            if not self._check_typesetter_condition():
                raise ValueError("Invalid state transition")
            if not self._check_file_condition():
                raise ValueError("Invalid file upload")
            # Check if there are any files already associated
            if self.assignment.files_to_typeset.exists():
                self._delete_core_files_record()
                self._remove_file_from_assignment()

            uploaded_file = save_file_to_article(self.file_to_upload, self.assignment.round.article, self.typesetter)
            try:
                self._update_typesetting_assignment(uploaded_file)
                self._look_for_queries_in_archive()
            except Exception as e:
                raise ValidationError(str(e)) from e
        return self.assignment.round.article


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

    def _generate_automatic_preamble(self):
        try:
            automatic_preamble_text = LatexPreamble.objects.get(journal=self.workflow.article.journal).preamble
        except LatexPreamble.DoesNotExist:
            logger.error(f"Missing preamble template for {self.workflow.article.journal.code}.")
            automatic_preamble_text = (
                f"Missing preamble template for {self.workflow.article.journal.code}\nPlease contact assistance.\n"
            )
        context = {
            "journal": self.workflow.article.journal,
            "article": self.workflow.article,
        }
        rendered_preamble = render_template(automatic_preamble_text, context)
        # TODO: refactor with utils.guess_tex_filename()
        preamble_name = f"{self.workflow.article.journal.code.lower()}-{self.workflow.article.id}-preamble.tex"
        return rendered_preamble, preamble_name

    def _create_archive(self, files):
        """Create a ZIP archive from the given files."""
        in_memory = BytesIO()
        with ZipFile(in_memory, "w") as archive:
            for file in files:
                file_path = file.self_article_path()
                archive.write(file_path, arcname=file.original_filename)
            automatic_preamble, preamble_name = self._generate_automatic_preamble()
            archive.writestr(preamble_name, automatic_preamble)

        in_memory.seek(0)
        return in_memory

    def run(self):
        """Serve the archive for download."""
        files = self._gather_files()
        archive = self._create_archive(files)

        return archive.getvalue()


@dataclasses.dataclass
class HandleCreateSupplementaryFile:
    """Handle the creation and upload of supplementary files."""

    file: File
    article: Article
    user: Account

    def _create_file_instance(self):
        file_instance = save_file_to_article(self.file, self.article, self.user)
        return file_instance

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.article.articleworkflow, self.user)

    def run(self):
        with transaction.atomic():
            if not self._check_typesetter_condition():
                raise ValueError("Invalid state transition")

            file_instance = self._create_file_instance()
            file_instance.save()

            supplementary_file = SupplementaryFile(file=file_instance)
            supplementary_file.save()

            self.article.supplementary_files.add(supplementary_file)

        return self.article


@dataclasses.dataclass
class HandleDeleteSupplementaryFile:
    """Handle the deletion of supplementary files."""

    supplementary_file: SupplementaryFile
    article: Article
    user: Account

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.article.articleworkflow, self.user)

    # We don't check for archival model references, we disassociate the file from the article. In the article's status
    # page we still show a list of supplementary files at acceptance.
    def run(self):
        with transaction.atomic():
            if not self._check_typesetter_condition():
                raise ValueError("Invalid state transition")
            self.supplementary_file.file.unlink_file()
            self.article.supplementary_files.remove(self.supplementary_file)
        return


def check_annotated_file_conditions(user: Account, galleyproofing: GalleyProofing) -> bool:
    """Check if annotated files (proofed files) can be created or deleted.

    This check is used in HandleCreateAnnotatedFile and HandleDeleteAnnotatedFile.
    """
    article = galleyproofing.round.article
    article_author = galleyproofing.proofreader == user
    check_state = (
        article.articleworkflow.state == ArticleWorkflow.ReviewStates.PROOFREADING and article.stage == STAGE_PROOFING
    )
    last_galleyproofing = (
        galleyproofing
        == GalleyProofing.objects.filter(
            round__article=article,
            proofreader=user,
        )
        .order_by("round__round_number")
        .last()
    )
    return article_author and check_state and last_galleyproofing


@dataclasses.dataclass
class HandleCreateAnnotatedFile:
    """
    Handle the creation and upload of proof/annotated files.
    When a paper is in stage "proofing", the author can add some files indicating corrections.
    """

    file: File
    galleyproofing: GalleyProofing
    user: Account

    def _create_file_instance(self):
        file_instance = save_file_to_article(
            self.file,
            self.galleyproofing.round.article,
            self.galleyproofing.proofreader,
        )
        return file_instance

    def run(self):
        with transaction.atomic():
            if not check_annotated_file_conditions(self.user, self.galleyproofing):
                raise ValueError("Cannot create files. Please contact the editorial office.")

            file_instance = self._create_file_instance()
            file_instance.save()

            self.galleyproofing.annotated_files.add(file_instance)

        return self.galleyproofing


@dataclasses.dataclass
class HandleDeleteAnnotatedFile:
    """Handle the deletion of proof/annotated files."""

    file_id: int
    galleyproofing: GalleyProofing
    user: Account

    def run(self):
        with transaction.atomic():
            if not check_annotated_file_conditions(self.user, self.galleyproofing):
                raise ValueError("Cannot delete files. Please contact the editorial office.")
            self.file = get_object_or_404(JanewayFile, pk=self.file_id)
            self.galleyproofing.annotated_files.remove(self.file)
            self.file.delete()
        return


@dataclasses.dataclass
class AuthorSendsCorrections:
    """The author sends a request for corrections. The article goes back to the typesetter."""

    user: Account
    old_assignment: TypesettingAssignment
    request: HttpRequest

    def _check_data_provided(self):
        """At least one file or a note must be provided."""
        self.galleyproofing = self.old_assignment.round.galleyproofing_set.first()
        return self.galleyproofing.annotated_files.exists() or self.galleyproofing.notes

    def _check_conditions(self):
        """Check if the conditions for the assignment are met."""
        self.article = self.old_assignment.round.article
        author_is_author = is_article_author(self.article.articleworkflow, self.user)
        state_conditions = can_proceed(self.article.articleworkflow.author_sends_corrections)
        return author_is_author and state_conditions

    def _create_typesetting_round(self):
        typesetting_round, _ = TypesettingRound.objects.get_or_create(
            article=self.article,
            round_number=self.old_assignment.round.round_number + 1,
        )
        return typesetting_round

    def _assign_typesetter(self) -> TypesettingAssignment:
        typesetting_assignment = TypesettingAssignment.objects.create(
            round=self._create_typesetting_round(),
            typesetter=self.old_assignment.typesetter,
            accepted=timezone.now(),
            due=timezone.now() + timezone.timedelta(days=settings.TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS),
        )
        return typesetting_assignment

    def _update_state(self):
        """Run FSM transition."""
        self.article.articleworkflow.author_sends_corrections()
        # we assume that, if the author sends back the paper to the typesetter (instead of sending it directly to
        # ready-for-publication), then some change is necessary and it is ok for us to reset the flag
        self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.NOT_TESTED
        self.article.articleworkflow.save()
        self.article.stage = STAGE_TYPESETTING
        self.article.save()
        self.galleyproofing.completed = timezone.now()
        self.galleyproofing.save()

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "article": self.article,
            "typesetter": self.old_assignment.typesetter,
        }

    def _log_operation(self, context) -> Message:
        """Log the operation."""
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="author_sends_corrections_subject",
            journal=self.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="author_sends_corrections_body",
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
                self.old_assignment.typesetter,
            ],
            verbosity=Message.MessageVerbosity.FULL,
            flag_as_read=False,
            flag_as_read_by_eo=True,
        )
        return message

    def run(self) -> TypesettingAssignment:
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            if not self._check_data_provided():
                raise ValueError("Data not provided")
            assignment = self._assign_typesetter()
            self._update_state()
            context = self._get_message_context()
            self._log_operation(context=context)
            return assignment


@dataclasses.dataclass
class TogglePublishableFlag:
    workflow: ArticleWorkflow

    def _check_conditions(self):
        return self.workflow.state in [
            ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
            ArticleWorkflow.ReviewStates.PROOFREADING,
        ]

    def _toggle_publishable_flag(self):
        self.workflow.production_flag_no_checks_needed = not self.workflow.production_flag_no_checks_needed
        self.workflow.save()

    def run(self):
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self._toggle_publishable_flag()
        return self.workflow


@dataclasses.dataclass
class AttachGalleys:
    """Attach some galley files to an Article.

    Expect to find one HTML and one EPUB in `path`.

    For HTML, scrape the source for <img> tags and look for the
    src files (as in <img src=...>) inside `path`.
    """

    archive_with_galleys: bytes  # usually a zip/tar.gz file containing the raw galley files processed by jcomassistant
    article: Article
    request: HttpRequest
    path: Path = dataclasses.field(init=False)  # path of the tmpdir where the upack method unpacked the received files

    def unpack_targz_from_jcomassistant(self) -> Path:
        """Unpack a tar.gz.

        Create and use a temporary folder.
        The caller should clean up if necessary.
        """
        unpack_dir = tempfile.mkdtemp()
        # Use BytesIO to treat bytes data as a file
        with BytesIO(self.archive_with_galleys) as file_obj:
            # Open the tar.gz archive
            with tarfile.open(fileobj=file_obj, mode="r:gz") as tar:
                # Extract all contents into the unpack directory
                tar.extractall(path=unpack_dir)
        unpack_dir = Path(unpack_dir)

        logger.debug(f"...jcomassistant processed files are in {unpack_dir}.")
        self.path = unpack_dir
        return self.path

    def unpack_zip_from_jcomassistant(self) -> Path:
        """Unpack a zip

        Create and use a temporary folder.
        The caller should clean up if necessary.
        """
        unpack_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(BytesIO(self.archive_with_galleys)) as archive:
            archive.extractall(unpack_dir)

        unpack_dir = Path(unpack_dir)

        logger.debug(f"...jcomassistant processed files are in {unpack_dir}.")
        self.path = unpack_dir
        return self.path

    def reemit_info_and_up(self, unpack_dir: Path) -> bool:
        """Emit as log messages lines read from the given log file.

        Expect the logfile to contain log-formatted lines suchs as:
        DEBUG From: ...

        Re-emit only info, wraning, error and critical.

        Also return if any error or critical was found (return True if all is good).
        """
        has_error_or_critical = False
        # log files are called something like
        # - galley-xxx.epub_log
        # - galley-xxx.html_log
        # - galley-xxx.srvc_log
        # We are going to use only the service log (*.srvc_log)
        srvc_log_files = list(unpack_dir.glob("galley-*.srvc_log"))
        if len(srvc_log_files) != 1:
            logger.warning(f"Found {len(srvc_log_files)} srvc_log files. Ask Elia")
            if len(srvc_log_files) == 0:
                return True
        srvc_log_file = srvc_log_files[0]
        with open(srvc_log_file) as log_file:
            for line in log_file:
                if line.startswith("INFO"):
                    logger.info(f"JA {line[11:-1]}")
                elif line.startswith("WARNING"):
                    logger.warning(f"JA {line[14:-1]}")
                elif line.startswith("ERROR"):
                    logger.error(f"JA {line[12:-1]}")
                    has_error_or_critical = True
                elif line.startswith("CRITICAL"):
                    logger.critical(f"JA {line[15:-1]}")
                    has_error_or_critical = True
                elif line.startswith("DEBUG"):
                    logger.debug(f"JA {line[12:-1]}")
        return not has_error_or_critical

    def _check_conditions(self) -> Tuple[bool, Optional[str]]:
        """
        Check for errors in the log files and if the expected files exist.

        We should get at least one PDF, one html and one epub file.
        """
        # NB: self.path is set in the run() method after unpacking the processed files received from jcomassistant
        if not self.reemit_info_and_up(self.path):
            return (False, "Errors found during generation.")
        for extension in ("html", "epub", "pdf"):
            if not any(self.path.glob(f"*.{extension}")):
                return (False, f"Missing {extension} file")
        return (True, None)

    def download_and_store_article_file(self, image_source_url: Path):
        """Downaload a media file and link it to the article."""
        if not image_source_url.exists():
            logger.error(f"Img {image_source_url.resolve()} does not exist. {os.getcwd()=}")
        image_name = image_source_url.name
        image_file = File(open(image_source_url, "rb"), name=image_name)
        new_file: JanewayFile = save_galley_image(
            self.article.get_render_galley,
            request=self.request,
            uploaded_file=image_file,
            label=image_name,  # [*]
        )
        # [*] I tryed to look for some IPTC metadata in the image
        # itself (Exif would probably be useless as it is mostly related
        # to the picture technical details) with `exiv2 -P I ...`, but
        # found 3 maybe-useful metadata on ~1600 files and abandoned
        # this idea.
        return new_file

    def mangle_images(self):
        """Download all <img>s in the article's galley and adapt the "src" attribute."""
        render_galley = self.article.get_render_galley
        galley_file: JanewayFile = render_galley.file
        galley_string: str = galley_file.get_file(self.article)
        html: HtmlElement = lxml.html.fromstring(galley_string)
        images = html.findall(".//img")
        for image in images:
            img_src = image.attrib["src"].split("?")[0]
            img_src = self.path / img_src
            img_obj: JanewayFile = self.download_and_store_article_file(img_src)
            # TBV: the `src` attribute is relative to the article's URL
            image.attrib["src"] = img_obj.label

        with open(galley_file.self_article_path(), "wb") as out_file:
            out_file.write(lxml.html.tostring(html, pretty_print=False))

    def save_html(self):
        """Set the first html file as HTML galley.

        Process it to adapt to our web page (drop how-to-cite, etc.)
        and deal with possible images.
        """
        html_galley_filename = [f for f in self.path.iterdir() if f.suffix == ".html"][0]
        html_galley_text = open(html_galley_filename).read()

        galley_language = evince_language_from_filename_and_article(str(html_galley_filename), self.article)
        processed_html_galley_as_bytes = process_body(html_galley_text, style="wjapp", lang=galley_language)
        name = "body.html"
        html_galley_file = File(BytesIO(processed_html_galley_as_bytes), name)
        label = "HTML"
        galley = save_galley(
            self.article,
            request=self.request,
            uploaded_file=html_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
            html_prettify=False,
        )
        self._check_html_galley_mimetype(galley)
        self.mangle_images()
        return galley

    def _check_html_galley_mimetype(self, galley: Galley):
        expected_mimetype = "text/html"
        acceptable_mimetypes = [
            "text/plain",
        ]
        if galley.file.mime_type != expected_mimetype:
            if galley.file.mime_type not in acceptable_mimetypes:
                logger.warning(f"Wrong mime type {galley.file.mime_type} for {galley}")
            galley.file.mime_type = expected_mimetype
            galley.file.save()
        self.article.render_galley = galley
        self.article.save()

    def save_epub(self):
        """Set the first epub file as EPUB galley."""
        epub_galley_filename = [f for f in self.path.iterdir() if f.suffix == ".epub"][0]
        epub_galley_file = File(open(epub_galley_filename, "rb"), name=epub_galley_filename.name)
        file_mimetype = "application/epub+zip"
        label, language = decide_galley_label(file_name=str(epub_galley_filename), file_mimetype=file_mimetype)
        galley = save_galley(
            self.article,
            request=self.request,
            uploaded_file=epub_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
        )
        logger.debug(f"EPUB galley {label} set onto {self.article.id}")
        return galley

    def save_pdf(self):
        """Set the first pdf file as PDF galley."""
        pdf_files = [f for f in self.path.iterdir() if f.suffix == ".pdf"]
        if len(pdf_files) != 1:
            # TODO: temporary workaround! In production, this should trigger a stopping error
            logger.error(f"Cannot find PDF in galleys of {self.article.id}")
            return
        pdf_galley_filename = pdf_files[0]
        pdf_galley_file = File(open(pdf_galley_filename, "rb"), name=pdf_galley_filename.name)
        file_mimetype = "application/pdf+zip"
        label, language = decide_galley_label(file_name=str(pdf_galley_filename), file_mimetype=file_mimetype)
        galley = save_galley(
            self.article,
            request=self.request,
            uploaded_file=pdf_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=True,
        )
        logger.debug(f"PDF galley {label} set onto {self.article.id}")
        return galley

    def run(self):
        # TODO: review me with specs#774: missing management of multilingual papers and PDF compilation
        # TODO: if targz: -> self.unpack_targz_from_jcomassistant()
        self.path = self.unpack_zip_from_jcomassistant()
        green_light, reason = self._check_conditions()
        if not green_light:
            self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_FAILED
            self.article.articleworkflow.save()
            self._notify_error(reason)
            # We save the given archive even if it has errors.
            # We save it in the filesystem among the other article files and return it as a Galley object, so that
            # our caller can process it easily (generally it will be linked to the TA or in the Article.galleys)
            jcomassistant_response_content = File(
                BytesIO(self.archive_with_galleys),
                name="jcomassistant_response.tar.gz",
            )
            galleys_created = [save_galley(self.article, self.request, jcomassistant_response_content, False)]

        else:
            galleys_created = [self.save_epub(), self.save_html(), self.save_pdf()]
            self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
            self.article.articleworkflow.save()
        shutil.rmtree(self.path)
        return galleys_created

    def _notify_error(self, reason: str):
        logger.error(f"Galleys generation failed for {self.article.id}: {reason}")
        send_mail(
            f"{self.article} - galley unpacking and attachment failed",
            f"Please check JCOMAssistant response content.\n{reason}",
            None,
            [self.request.user.email],
            fail_silently=False,
        )


@dataclasses.dataclass
class TypesetterTestsGalleyGeneration:
    """Generate galleys for an article."""

    assignment: TypesettingAssignment
    request: HttpRequest  # Used in Janeway's save_galley, in log_operation and maybe in _check_conditions

    def _check_user_conditions(self):
        """Check if the user is article's typesetter."""
        return is_article_typesetter(self.assignment.round.article.articleworkflow, self.request.user)

    def _check_files_conditions(self):
        """Check if there are files to typeset."""
        return self.assignment.files_to_typeset.exists()

    def _check_conditions(self):
        """Check if the conditions for the galley generation are met."""
        return self._check_user_conditions() and self._check_files_conditions()

    def _clean_galleys(self) -> None:
        """
        Clean existing galleys in case typesetter needs to render them again in the same typesetting round.
        """
        self.assignment.galleys_created.all().delete()
        self.assignment.round.article.render_galley = None
        self.assignment.round.article.save()
        self.assignment.round.article.galley_set.all().delete()

    def _jcom_assistant_client(self):
        assistant = JcomAssistantClient(
            archive_with_files_to_process=self.assignment.files_to_typeset.first(), user=self.assignment.typesetter
        )
        response = assistant.ask_jcomassistant_to_process()
        return response

    def _get_message_context(self):
        """Get the context for the message template."""
        return {
            "article": self.assignment.round.article,
        }

    def _log_operation(self, context) -> Message:
        """Log the operation."""
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="typesetting_generated_galleys_subject",
            journal=self.assignment.round.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="typesetting_generated_galleys_body",
            journal=self.assignment.round.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message = communication_utils.log_operation(
            article=self.assignment.round.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[
                self.assignment.typesetter,
            ],
            verbosity=Message.MessageVerbosity.FULL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
        return message

    def _mock_jcom_assistant_client(self, path_to_mock_file):
        """
        Invoke :py:class:`AttachGalleys` with a mock JCOM Assistant response file to run the class without access
        to JCOM Assistant endpoint.
        """
        with open(path_to_mock_file, "rb") as f:
            response = f.read()
        return AttachGalleys(
            archive_with_galleys=response,
            article=self.assignment.round.article,
            request=self.request,
        ).run()

    def _get_galleys_from_jcom_assistant(self):
        """
        Use Jcom Assistent to render the galleys and attach them to the article.

        If settings.JCOMASSISTANT_MOCK_FILE is set, use the path as a mock response file instead of contacting
        the JCOM Assistant service.
        """
        # TODO: wrap in try/except and contact typ on errors
        # e.g. ConnectionError janeway-services.ud.sissamedialab.it Name or service not known
        if settings.JCOMASSISTANT_MOCK_FILE:
            return self._mock_jcom_assistant_client(settings.JCOMASSISTANT_MOCK_FILE)
        response = self._jcom_assistant_client()
        galleys_created = AttachGalleys(
            archive_with_galleys=response.content,
            article=self.assignment.round.article,
            request=self.request,
        ).run()
        return galleys_created

    def _generate_and_attach_galleys(self):
        """Generate galleys.

        - send source files to jcomassistant
        - attach generated galleys to TA (and article)
        - record success/failure of the generation
        """
        galleys_created = self._get_galleys_from_jcom_assistant()
        self.assignment.galleys_created.set(galleys_created)
        # This flag is set by AttachGalleys that we have just run
        if (
            self.assignment.round.article.articleworkflow.production_flag_galleys_ok
            == ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
        ):
            return True
        else:
            send_mail(
                f"{self.assignment.round.article} - galley unpacking and attachment failed",
                f"Please zip file in Assigment {self.assignment} galleys_created.",
                None,
                [self.request.user.email],
                fail_silently=False,
            )
            return False

    def _check_queries_in_tex_src(self):
        """Fixme.

        This is a stub. ATM, blindly record that there are not queries.

        TODO: fixme in specs#718
        """
        self.assignment.round.article.articleworkflow.production_flag_no_queries = True
        self.assignment.round.article.articleworkflow.save()
        logger.warning(f"{self.assignment.round.article.articleworkflow.production_flag_no_queries=}")

    def run(self) -> None:
        if not self._check_conditions():
            # This logic is generally called asynchronously, so we don't
            # raise an exception here, but directly notify the typesetter
            logger.error(f"Galley generation failed to start for article {self.assignment.round.article.id}")
            send_mail(
                f"{self.assignment.round.article} - galley generation failed to start",
                f"Please check\n{self.assignment.round.article.url}\n",
                None,
                [self.request.user.email],
                fail_silently=False,
            )
            return
        self._clean_galleys()
        if not self._generate_and_attach_galleys():
            return
        self._check_queries_in_tex_src()
        context = self._get_message_context()
        self._log_operation(context)


@dataclasses.dataclass
class JcomAssistantClient:
    """Client for JCOM Assistant."""

    archive_with_files_to_process: File  # Usually a zip/tar.gz file object containing the TeX source files to process
    user: Account

    def ask_jcomassistant_to_process(self) -> requests.Response:
        """Send the given zip file to jcomassistant for processing.

        Return the path to a folder with the unpacked response.
        """
        url = settings.JCOMASSISTANT_URL
        logger.debug(f"Contacting jcomassistant service at {url}...")

        # TODO: please decide what you want!
        if isinstance(self.archive_with_files_to_process, JanewayFile):  # File???
            file_path = self.archive_with_files_to_process.self_article_path()
        elif isinstance(self.archive_with_files_to_process, Path):
            file_path = self.archive_with_files_to_process
        else:
            raise NotImplementedError(
                f"Don't know how to open {type(self.archive_with_files_to_process)} for jcomassistant processing!",
            )

        files = {"file": open(file_path, "rb")}
        response = requests.post(url=url, files=files)
        if response.status_code != 200:
            logger.error("Unexpected status code {response.status_code}. Trying to proceed...")
            send_mail(
                f"{self.archive_with_files_to_process.article} - galley generation service failed",
                f"Please check JCOMAssistant service status.\n{response.content}",
                None,
                [self.user.email],
                fail_silently=False,
            )
            raise ValueError(f"Unexpected status code {response.status_code}.")
        return response


@dataclasses.dataclass
class ReadyForPublication:
    """Bring a paper in RFP state."""

    workflow: ArticleWorkflow
    user: Account

    def _check_conditions(self) -> bool:
        """Check that the FSM allows the transaction.

        Take the operator into consideration
        """
        # TODO: might want to verify some of the checks of specs#791 here
        if is_article_author(self.workflow, self.user):
            return can_proceed(self.workflow.author_deems_paper_ready_for_publication)
        elif is_article_typesetter(self.workflow, self.user):
            return can_proceed(self.workflow.typesetter_deems_paper_ready_for_publication)
        else:
            raise ValueError(f"Unexpected user attempting the transaction ({self.user=}).")

    def _update_state(self):
        """Run FSM transition."""
        if is_article_author(self.workflow, self.user):
            self.workflow.author_deems_paper_ready_for_publication()
        elif is_article_typesetter(self.workflow, self.user):
            self.workflow.typesetter_deems_paper_ready_for_publication()
        else:
            # should never be able to get here because _check_conditions is run berfore
            raise ValueError(f"Unexpected user attempting the transaction ({self.user=}). Possible programming error!")
        self.workflow.save()

        self.workflow.article.stage = STAGE_READY_FOR_PUBLICATION
        self.workflow.article.save()

    def run(self):
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Paper not yet ready for publication. For assitance, contact the EO.")
            self._update_state()
        return self.workflow


@dataclasses.dataclass
class HandleEOSendBackToTypesetter:
    workflow: ArticleWorkflow
    user: Account
    old_assignment: TypesettingAssignment
    body: str
    subject: str

    def _check_conditions(self) -> bool:
        is_user_eo = has_eo_role(self.user)
        check_state = self.workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION
        return is_user_eo and check_state

    def _update_state(self):
        """Run FSM transition."""
        self.workflow.admin_sends_back_to_typ()
        self.workflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.NOT_TESTED
        self.workflow.save()
        self.workflow.article.stage = STAGE_TYPESETTING
        self.workflow.article.save()

    def _create_typesetting_round(self):
        """Create a new typesetting round."""
        typesetting_round, _ = TypesettingRound.objects.get_or_create(
            article=self.workflow.article,
            round_number=self.old_assignment.round.round_number + 1,
        )
        return typesetting_round

    def _create_typesetting_assignment(self):
        """Create a new typesetting assignment."""
        TypesettingAssignment.objects.create(
            round=self._create_typesetting_round(),
            typesetter=self.old_assignment.typesetter,
            accepted=timezone.now(),
            due=timezone.now() + timezone.timedelta(days=settings.TYPESETTING_ASSIGNMENT_DEFAULT_DUE_DAYS),
        )

    def _log_operation(self) -> Message:
        """Log the operation."""
        message = communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=self.subject,
            message_body=self.body,
            actor=None,
            recipients=[
                self.old_assignment.typesetter,
            ],
            message_type=Message.MessageTypes.SYSTEM,
        )
        return message

    def run(self):
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self._update_state()
            self._create_typesetting_assignment()
            self._log_operation()
            return self.workflow


@dataclasses.dataclass
class BeginPublication:
    """Begin the publication process.

    The publication process is comprised of two steps:
    - begin publication
      - set the identifiers and publication date
      - adapt the source files with the identifiers
    - finish publication
      - generate the galleys
      - bump the article stage

    The second stage might be long (galley generation can last for even a minute) and could crash (most probably for
    some infrastructure temporary issue).

    Here we deal with the first stage and demand the second to another part of the logic.
    """

    workflow: ArticleWorkflow
    user: Account  # this user will be contacted is somwthing goes wrong during galley generation
    request: HttpRequest  # we'll end-up calling Janeway's save_galley_image(), that needs a request obj
    assignment: TypesettingAssignment = dataclasses.field(init=False)
    source_files: Path = dataclasses.field(init=False)

    def __post_init__(self):
        """Find the source files."""
        self.assignment = self.workflow.latest_typesetting_assignment()
        # The source files for the galley are in the latest typesetting assignment
        # Even if the field is a m2m, we alway set at most one item.
        self.source_files = Path(self.assignment.files_to_typeset.get().self_article_path())

    def check_conditions(self) -> Tuple[bool, Optional[str]]:
        if self.workflow.state not in [
            ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION,
        ]:
            return (False, "Paper not in expected state")
        if not self.workflow.can_be_set_rfp():
            return (False, "Paper not ready. Please check galleys or queries in the sources.")
        if self.workflow.article.primary_issue is None:
            return (False, "Paper has not issue associated.")
        return (True, None)

    def set_article_identifiers(self):
        """Set DOI and pubid and publication date."""
        if not self.workflow.article.date_published:
            self.workflow.article.date_published = timezone.now()
            self.workflow.article.save()
        self.workflow.set_doi()
        self.workflow.set_pubid()

    def prepare_source_files(self):
        """Apply identifiers and publication date to source files.

        We assume that the article already has pubid and DOI.
        Here we add these (and the publication date) to the TeX source.

        The prepared source files are then (saved to the filesystem) and linked to the
        article's source-files.
        """
        try:
            source_file = self._get_source_file()
            prepared_source_file = self._prepare_source(source_file)
            # TODO: save "historical" version of such file (see Janeway's file history) before modification
            self._store_prepared_source(prepared_source_file)
        except Exception as exception:
            raise ValueError(
                "Preparation of source files for final galley generation failed. Publication aborted."
                " You may want to send the paper back to the typesetter.\n"
                f" {exception}",
            )

    def _store_prepared_source(self, file_data: BytesIO, file_name: str = None):
        """Include the given file into the article source files zip, under the given file-name.

        Defaults to replacing the tex source file (i.e. the file name will be something like JCOM_123.tex).
        """
        # TODO: talk with Elian on the opportunity of buildind a "texfile utils" library with similar functions
        # TODO: refactor with utils.guess_tex_filename()
        file_name = (
            f"{self.workflow.article.journal.code}_{self.workflow.article.id}.tex" if file_name is None else file_name
        )
        tempfiledesc, tempfilename = tempfile.mkstemp(dir=self.source_files.parent)
        originalfile_was_in_archive = False
        with zipfile.ZipFile(self.source_files, "r") as original_zip:
            with zipfile.ZipFile(tempfilename, "w") as new_zip:
                for item in original_zip.infolist():
                    if item.filename != file_name:
                        new_zip.writestr(item, original_zip.read(item.filename))
                    else:
                        originalfile_was_in_archive = True
                new_zip.writestr(zinfo_or_arcname=file_name, data=file_data.read())

        # Sanity check: we usually expect to replace a file that already exists in the sources archive
        if not originalfile_was_in_archive:
            logger.warning(
                f"Cannot find {file_name} in archive {self.source_files} for article {self.workflow}."
                " File added to archive and hoping for the best.",
            )

        # Replace the article.source_files (only one item) with the just modified sources
        # TODO: we could store the source files in two places:
        # - article.source_files
        # - last TA.files_to_typeset
        # now we use TA, because we must keep a history of them source files.
        # But we should probably
        # - keep the latest sources on the article
        # - keep the history in the TA:
        #   every time the typ UploadFile,
        #   store the reference to the files bot in TA and article

        final_sources: JanewayFile = save_file_to_article(
            file_to_handle=File(open(tempfilename, "rb"), name=file_name.replace(".tex", ".zip")),
            article=self.workflow.article,
            owner=self.user,
            label="Final sources",
            description="Source files for final galleys",
            replace=None,
        )
        assert self.workflow.article.source_files.count() == 1, "Too many source files. Expected exactly one!"
        self.workflow.article.source_files.first().delete()  # TODO: verify that `delete` also unlinks!
        self.workflow.article.source_files.set((final_sources,))
        os.unlink(tempfilename)

    def _prepare_source(self, source_file: BytesIO) -> BytesIO:
        r"""Set pubid, DOI and publication date into the given file and return it.

        Placeholders are expected as follow:
        \published{???}
        \publicationyear{xxxx}
        \publicationvolume{xx}
        \publicationissue{xx}
        \publicationnum{xx}
        \doiInfo{https://doi.org/}{doi}

        """
        publication_date = self.workflow.article.date_published.strftime("%Y-%m-%d")
        publication_year = self.workflow.article.date_published.year
        volume = f"{self.workflow.article.primary_issue.volume:02d}"
        # TODO: can it ever happen that issue.issue is not in the form "01"?
        issue = f"{int(self.workflow.article.primary_issue.issue):02d}"
        # Page numbers should have been set when we set the pubid when we do set_article_identifiers()
        num = self.workflow.page_numbers
        doi = self.workflow.article.get_doi()

        # Please keep coherent with conftest.jcom_automatic_preamble for documentation.
        replacements = (
            # f-strings and latex macros don't dance well together...
            (r"\published{???}", rf"\published{{{publication_date}}}"),
            (r"\publicationyear{xxxx}", rf"\publicationyear{{{publication_year}}}"),
            (r"\publicationvolume{xx}", rf"\publicationvolume{{{volume}}}"),
            (r"\publicationissue{xx}", rf"\publicationissue{{{issue}}}"),
            (r"\publicationnum{xx}", rf"\publicationnum{{{num}}}"),
            (r"\doiInfo{https://doi.org/}{doi}", rf"\doiInfo{{https://doi.org/{doi}}}{{{doi}}}"),
        )

        source_file.seek(0)
        # we can safely assume that we are dealing with a utf8-encoded text file
        content = source_file.read().decode("utf-8")

        # TODO: should I expect to always find all replacement?
        # I.e. is it an error if some replacement cannot be found in the source?
        for old_string, new_string in replacements:
            content = content.replace(old_string, new_string, 1)
        processed_file = BytesIO(content.encode("utf-8"))
        return processed_file

    def _get_source_file(self) -> BytesIO:
        """Extract the source file of the article galleys.

        Return the main TeX file, the one that contains the LaTeX preamble.
        """
        # TODO: talk with Elia on the opportunity of buildind a "texfile utils" library with similar functions
        # TODO: refactor with utils.guess_tex_filename()
        tex_source_name = f"{self.workflow.article.journal.code}_{self.workflow.article.id}.tex"
        # TODO: ask Elia: is zip-file correct? should it be tar.gz? maybe both?
        with zipfile.ZipFile(self.source_files) as zip_file:
            if tex_source_name in zip_file.namelist():
                main_tex = zip_file.open(tex_source_name)
            else:
                raise FileNotFoundError(
                    f"Cannot read {tex_source_name} from archive {self.source_files} for article {self.workflow}",
                )
        return main_tex

    def update_state(self):
        """Bumb the state (but not the stage)."""
        self.workflow.begin_publication()

    def trigger_galley_generation(self):
        """Trigger an async process for the galley generation."""
        async_task(finishpublication_wrapper, workflow_pk=self.workflow.pk, user_pk=self.user.pk)

    def run(self):
        with transaction.atomic():
            green_light, reason = self.check_conditions()
            if not green_light:
                raise ValueError(f"Paper cannot be published. {reason}")
            self.set_article_identifiers()
            self.prepare_source_files()
            self.update_state()
        # We bump the state _before_ triggering the galley generation,
        # because once the article is in publication-in-progress,
        # the galley generation can always be triggered.
        #
        # Also, we keep the trigger outside the transition, else
        # risk re-winding the transaction if the trigger fails.
        #
        # If we do vice-versa (trigger and then bump) and the bump fails,
        # the transaction can be re-winded, but the
        # galley generation has already been started and is proceeding.
        self.trigger_galley_generation()
        return self.workflow


@dataclasses.dataclass
class FinishPublication:
    """Conclude the publication process.

    This mean
    - generate the galleys
    - bump the state/stage
    - notify who needs to be notified.
    """

    workflow: ArticleWorkflow
    user: Account  # this user will be contacted is somwthing goes wrong during galley generation
    request: HttpRequest  # we'll end-up calling Janeway's save_galley_image(), that needs a request obj

    def check_conditions(self) -> Tuple[bool, Optional[str]]:
        if self.workflow.state not in [
            ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS,
        ]:
            return (False, "Paper not in expected state")
        return (True, None)

    # TODO: refactor with TypesetterTestsGalleyGeneration methods
    def _jcom_assistant_client(self) -> requests.Response:
        assistant = JcomAssistantClient(
            archive_with_files_to_process=self.workflow.article.source_files.first(),
            user=self.user,
        )
        response = assistant.ask_jcomassistant_to_process()
        return response

    # TODO: refactor with TypesetterTestsGalleyGeneration methods
    # here I've changed some variables names and the mail message
    def generate_final_galleys(self):
        response = self._jcom_assistant_client()
        galleys_created = AttachGalleys(
            archive_with_galleys=response.content,
            article=self.workflow.article,
            request=self.request,
        ).run()
        self.workflow.article.galley_set.set(galleys_created)
        # This flag is set by AttachGalleys that we have just run
        if self.workflow.production_flag_galleys_ok == ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED:
            return True
        else:
            send_mail(
                f"{self.workflow.article} - final galley generation failed",
                """Please note that the generation has been attempted on the article sources,
and this have been automatically derived from the latest typesetted files.

This is usually related to some temporary issue with the infrastructure.

Please retry and contact assistance is the problem persists.
""",
                None,
                [self.request.user.email],
                fail_silently=False,
            )
            return False

    def update_state(self):
        """Bumb state and stage."""
        self.workflow.finish_publication()
        # Apply Janeway logic (snapshot authors etc.)

        # TODO: in import_utils, we verify the article's issue's date against the article publication date. This makes
        # sense in the context of setting some metadata on the issue that we did not have before, but does it makes
        # sense here also?

        # ... if article.date_published < article.issue.date_published:
        # ...   article.issue.date = article.date_published

        # Also, there might be reasons to snapshot the authors before,
        # i.e. when the identifiers are set.
        import_utils.publish_article(self.workflow.article)

        self._trigger_workflow_event()

    def _trigger_workflow_event(self):
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""
        workflow_kwargs = {
            "handshake_url": "wjs_review_list",
            "request": self.request,
            "article": self.workflow.article,
            "switch_stage": True,
        }
        events_logic.Events.raise_event(
            events_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE,
            task_object=self.workflow.article,
            **workflow_kwargs,
        )

    def run(self):
        with transaction.atomic():
            green_light, reason = self.check_conditions()
            if not green_light:
                raise ValueError(f"Final falley generation cannot be started. {reason}")
            if self.generate_final_galleys():
                self.update_state()
        return self.workflow


def finishpublication_wrapper(workflow_pk: int, user_pk: int):
    """Wrap the call to FinishSubmission to allow for async processing."""
    # Please note that we cannot directly use the real request object because
    # cannot pickle '_io.BufferedReader' object
    user = Account.objects.get(pk=user_pk)
    workflow = ArticleWorkflow.objects.get(pk=workflow_pk)
    request = create_rich_fake_request(journal=workflow.article.journal, settings=settings, user=user)

    FinishPublication(
        workflow=workflow,
        user=user,
        request=request,
    ).run()
