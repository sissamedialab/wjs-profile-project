"""
Logic classes for production-related actions & co.

This module should be *-imported into logic.py
"""

import dataclasses
import datetime
import os
import re
import shutil
import tarfile
import tempfile
import traceback
import zipfile
from dataclasses import dataclass
from io import BytesIO
from itertools import permutations
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlencode
from zipfile import ZipFile

import lxml.html
import pycountry
import requests
from core.files import save_file_to_article
from core.models import File as JanewayFile
from core.models import Galley, SupplementaryFile
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.db.models import Case, IntegerField, OuterRef, QuerySet, Subquery, When
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone, translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django_fsm import can_proceed
from django_q.tasks import async_task
from events import logic as events_logic
from identifiers.logic import get_dois_for_articles
from identifiers.models import Identifier
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
    ArticleAuthorOrder,
    Keyword,
    KeywordArticle,
)
from utils.logger import get_logger
from utils.management.commands.test_fire_event import create_fake_request
from utils.setting_handler import get_setting

from wjs.jcom_profile import import_utils
from wjs.jcom_profile.import_utils import (
    decide_galley_label,
    evince_language_from_filename_and_article,
    process_body,
    sync_frozen_authors_with_authors,
)
from wjs.jcom_profile.models import Correspondence
from wjs.jcom_profile.permissions import has_eo_role
from wjs.jcom_profile.utils import (
    create_rich_fake_request,
    get_eo_user,
    render_template,
    render_template_from_setting,
)

from . import communication_utils
from .models import ArticleWorkflow, LatexPreamble, Message
from .permissions import (
    has_typesetter_role_by_article,
    is_article_author,
    is_article_supervisor,
    is_article_typesetter,
)
from .utils import (
    get_tex_source_file_path_from_archive,
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
                get_eo_user(self.articleworkflow.article),
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
        # TODO: please comment on why we use get_or_create();
        #       IIC, get() should be sufficient.
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
            setting_group_name="email_subject",
            setting_name="subject_typesetter_notification",
            journal=self.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="typesetter_notification",
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
            verbosity=Message.MessageVerbosity.TIMELINE,
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
            actor=self.typesetter,
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


def typesettertestsgalleygeneration_wrapper(
    assignment_id: int,
):
    """Wrap the call to :py:class:`TypesetterTestsGalleyGeneration` to allow for async processing."""
    # See also logic__production.finishpublication_wrapper().
    assignment = get_object_or_404(TypesettingAssignment, pk=assignment_id)
    request = create_fake_request(user=assignment.typesetter, journal=assignment.round.article.journal)

    try:
        logic_instance = TypesetterTestsGalleyGeneration(
            assignment=assignment,
            request=request,
        )
        logic_instance.run()
    except Exception as e:
        article = assignment.round.article  # just an alias
        msg = str(e)
        msg += "\n\n"
        msg += traceback.format_exc()
        msg = msg.replace("\n", "<br>")
        communication_utils.notify_async_event(
            message_subject="Galleys generation error",
            message_body=msg,
            recipients=[assignment.typesetter],
            article=article,
        )

        article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_FAILED.value
        article.articleworkflow.save()


@dataclasses.dataclass
class TypesettedFilesUpload:
    """Allow the typesetter to upload typesetted files."""

    typesetter: Account
    request: HttpRequest
    assignment: TypesettingAssignment
    file_to_upload: File
    do_create_galleys: bool = True

    VALID_FILE_TYPES = ["application/zip", "application/x-zip-compressed"]

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.assignment.round.article.articleworkflow, self.request.user)

    def _check_state_condition(self):
        return self.assignment.round.article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED

    def _check_file_condition(self):
        return self.file_to_upload and self.file_to_upload.content_type in self.VALID_FILE_TYPES

    def _remove_file_from_assignment(self):
        """Empty the files_to_typeset field of TypesettingAssignment."""
        self.assignment.files_to_typeset.clear()

    def _delete_core_files_record(self):
        """Delete the current source files and reset the flag to galleys-untested."""
        file_record = self.assignment.files_to_typeset.get()
        file_record.delete()
        self.assignment.round.article.articleworkflow.production_flag_galleys_ok = (
            ArticleWorkflow.GalleysStatus.NOT_TESTED
        )
        self.assignment.round.article.articleworkflow.save()

    def _update_typesetting_assignment(self, uploaded_file):
        """Create the relation in files_to_typeset field of TypesettingAssignment."""
        self.assignment.files_to_typeset.add(uploaded_file)

    def _look_for_queries_in_archive(self):
        """Check if there are any queries in the archive's source tex file."""
        filename = guess_typesetted_texfile_name(self.assignment.round.article)
        tmpdir = get_tex_source_file_path_from_archive(self.file_to_upload, filename)
        tex_file = os.path.join(tmpdir, filename)
        self.assignment.round.article.articleworkflow.production_flag_no_queries = not tex_file_has_queries(tex_file)
        self.assignment.round.article.articleworkflow.save()
        shutil.rmtree(tmpdir)

    def run(self):
        """Execute the file upload logic."""
        with transaction.atomic():
            if not self._check_typesetter_condition():
                raise ValueError("Invalid actor")
            if not self._check_state_condition():
                raise ValueError("Invalid article state")
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
        if self.do_create_galleys:
            try:
                async_task(
                    typesettertestsgalleygeneration_wrapper,
                    self.assignment.pk,
                    task_name="test-galley-generation",
                )
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
        rendered_preamble = self.render_latexpreamble(automatic_preamble_text, context)
        # TODO: refactor with utils.guess_tex_filename()
        preamble_name = f"{self.workflow.article.journal.code.lower()}-{self.workflow.article.id}-preamble.tex"
        return rendered_preamble, preamble_name

    @staticmethod
    def render_latexpreamble(preamble: str, context: dict) -> str:
        r"""
        Render the latex preamble, applying pre- and post-processing due to curly braces.

        In a template for latex code, we can have fragments such as
        \author{{{ account.fullname }}}
        where the first "{" is for the tex macro
        and the next two "{{" are for the template variable.

        Same for
        \keywords{{% for ... {% endfor %}}

        These fragments cannot be parsed/rendered,
        so we replace the outer curly brace with a placeholder
        to be removed after the rendering.
        """
        preamble = preamble.replace("{{{", "🙂{{").replace("}}}", "}}🙁").replace("{{%", "🙂{%").replace("%}}", "%}🙁")
        preamble = render_template(preamble, context)
        return preamble.replace("🙂", "{").replace("🙁", "}")

    def _create_archive(self, files):
        """Create a ZIP archive from the given files."""
        in_memory = BytesIO()
        with ZipFile(in_memory, "w") as archive:
            for file in files:
                file_path = file.self_article_path()
                # This is a workaround for local development when working with databases dumped from production / dev
                # to avoid the need to have the actual files on the local machine.
                if getattr(settings, "WJS_TYPESET_REVISION_MOCK_FILE", None):
                    archive.write(settings.WJS_TYPESET_REVISION_MOCK_FILE, arcname=file.original_filename)
                else:
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
    label: str | None

    def _create_file_instance(self):
        file_instance = save_file_to_article(self.file, self.article, self.user, self.label)
        return file_instance

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.article.articleworkflow, self.user) or is_article_supervisor(
            self.article.articleworkflow, self.user
        )

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
        return is_article_typesetter(self.article.articleworkflow, self.user) or is_article_supervisor(
            self.article.articleworkflow, self.user
        )

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
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_notify_typesetter_proofing_changes",
            journal=self.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="notify_typesetter_proofing_changes",
            journal=self.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message = communication_utils.log_operation(
            article=self.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=self.user,
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

    archive_with_galleys: bytes
    "Usually a zip/tar.gz file containing the raw galley files processed by jcomassistant"

    article: Article
    request: HttpRequest

    public_galley: bool = False
    """Control the public flag of the galleys.

    Galleys have a "public" flag, that, together with the "article" FK, control if the galley should be visible in the
    paper's landing page.

    """

    path: Path = dataclasses.field(init=False)
    "Path of the tmpdir where the upack-method unpacked the received files"

    def unpack_targz_from_jcomassistant(self) -> Path:
        """
        Unpack a tar.gz.

        Create and use a temporary folder.
        The caller should clean up if necessary.
        """
        # FIXME: this method is not currently used
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
        """
        Unpack a zip.

        Create and use a temporary folder.
        The caller should clean up if necessary.
        """
        self.path = tempfile.mkdtemp()
        with zipfile.ZipFile(BytesIO(self.archive_with_galleys)) as archive:
            archive.extractall(self.path)

        self.path = Path(self.path)

        logger.debug(f"...jcomassistant processed files are in {self.path}.")
        return self.path

    def reemit_info_and_up(self, unpack_dir: Path) -> bool:
        """
        Emit as log messages lines read from the given log file.

        Expect the logfile to contain log-formatted lines suchs as:
        DEBUG From: ...

        Re-emit only info, wraning, error and critical.

        Also return if any error or critical was found (return True if all is good).
        """
        has_error_or_critical = False
        # log files are called something like
        # - galleyXXX.epub_log
        # - galleyXXX.html_log
        # - galleYXXX.srvc_log
        # We are going to use only the service log (*.srvc_log)
        srvc_log_files = list(unpack_dir.glob("galley*.srvc_log"))
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

    def _check_conditions(self):
        """
        Check for errors in the log files and if the expected files exist.

        We should get at least one PDF, one HTML and one EPUB file.

        Raises:
            ValueError: if there were any errors upstream

            FileNotFoundError: if any of the expected file could not be found

        """
        # NB: self.path is set in the run() method after unpacking the processed files received from jcomassistant
        if not self.reemit_info_and_up(self.path):
            msg = f"Errors found during galley generation for {self.article.id}."
            # We do not log any message, because it is common to have some errors in the initial phases of typesetting
            raise ValueError(msg)
        for extension in ("html", "epub", "pdf"):
            if not any(self.path.glob(f"*.{extension}")):
                msg = f"Missing galley with extension {extension} for {self.article.id}."
                # If this happens there might be some kind of miss-understanding with upstream:
                # if any file is missing, some error should have been reported!
                logger.error(msg)
                raise FileNotFoundError(msg)

    def store_galleyimage(self, image_pathname: Path, galley: Galley) -> JanewayFile:
        """Get the image from the processed archive, save it, and link it to the galley.

        Raises:
            FileNotFoundError: if the given path does not exist.

        """
        if not image_pathname.exists():
            archive_content = [str(p) for p in self.path.rglob("*")]
            msg = (
                f"Img {image_pathname.absolute()} does not exist"
                f" (article {self.article.id}; {'📁-'.join(archive_content)})"
            )
            logger.error(msg)
            raise FileNotFoundError(msg)
        image_name = image_pathname.name
        image_file = File(open(image_pathname, "rb"), name=image_name)
        new_file: JanewayFile = save_galley_image(
            galley=galley,
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

    def mangle_images(self, galley: Galley):
        """Download all <img>s in the galley and adapt the "src" attribute."""
        galley_file: JanewayFile = galley.file
        galley_string: str = galley_file.get_file(self.article)
        html: HtmlElement = lxml.html.fromstring(galley_string)
        images = html.findall(".//img")
        for image_element in images:
            # We expect the "src" attribute to contain a pathname (full path);
            # NB: this pathname existed when the img file was created on jcomassistant:
            #     it does not exist in the filesystem where we opened the processed archive!
            # It might also contain a query-string part: that we drop with the split("?")
            wrong_pathname = Path(image_element.attrib["src"].split("?")[0])
            # Expecting something like "/tmp/tmpabc/..."
            # the first 3 parts ("/", "tmp", "tmpabc") can be dropped
            img_src = self.path.joinpath(*wrong_pathname.parts[3:])
            img_obj = self.store_galleyimage(img_src, galley)
            # Remember that, in the HTML galley, the `src` attribute is relative to the article's URL
            image_element.attrib["src"] = img_obj.label

        with open(galley_file.self_article_path(), "wb") as out_file:
            out_file.write(lxml.html.tostring(html, pretty_print=False))

    def _get_singlegalley_path(self, suffix: str) -> Path:
        """
        Look in the processed archive and return the first galley with the given suffix.

        Raises:
            RuntimeError: if no galley with the given suffix can be found, because this should have already been
            verified by _check_conditions()

        """
        candidates = [f for f in self.path.iterdir() if f.suffix == suffix]
        if len(candidates) < 1:
            msg = f"No galley with suffix {suffix} for {self.article.id}! This should have already been checked."
            raise RuntimeError(msg)
        if len(candidates) > 1:
            # Not really an error, but this is not expected to happen and I don't want to miss it if it happens
            logger.error(f"Found {len(candidates)} files with suffix {suffix} for {self.article.id}")
        return candidates[0]

    def save_html(self):
        """Set the first html file as HTML galley.

        Process it to adapt to our web page (drop how-to-cite, etc.)
        and deal with possible images.
        """
        html_galley_filename = self._get_singlegalley_path(suffix=".html")
        html_galley_text = open(html_galley_filename).read()

        galley_language = evince_language_from_filename_and_article(str(html_galley_filename), self.article)
        processed_html_galley_as_bytes = process_body(html_galley_text, style="wjapp", lang=galley_language)

        name = "body.html"
        html_galley_file = File(BytesIO(processed_html_galley_as_bytes), name)
        label = "HTML"
        galley = save_galley(
            article=self.article,
            request=self.request,
            uploaded_file=html_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=self.public_galley,
            html_prettify=False,
        )
        self._check_html_galley_mimetype(galley)
        self.mangle_images(galley)
        logger.debug(f"HTML galley {label} set onto {self.article.id}")
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

    def save_epub(self):
        """Set the first epub file as EPUB galley."""
        epub_galley_filename = self._get_singlegalley_path(suffix=".epub")
        epub_galley_file = File(open(epub_galley_filename, "rb"), name=epub_galley_filename.name)
        file_mimetype = "application/epub+zip"
        label, _language = decide_galley_label(file_name=str(epub_galley_filename), file_mimetype=file_mimetype)
        galley = save_galley(
            article=self.article,
            request=self.request,
            uploaded_file=epub_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=self.public_galley,
        )
        logger.debug(f"EPUB galley {label} set onto {self.article.id}")
        return galley

    def save_pdf(self):
        """
        Set the first pdf file as PDF galley.

        Raises:
            ValueError: if the PDF galleys is missing.

        """
        pdf_galley_filename = self._get_singlegalley_path(suffix=".pdf")
        pdf_galley_file = File(open(pdf_galley_filename, "rb"), name=pdf_galley_filename.name)
        file_mimetype = "application/pdf+zip"
        label, _language = decide_galley_label(file_name=str(pdf_galley_filename), file_mimetype=file_mimetype)
        galley = save_galley(
            article=self.article,
            request=self.request,
            uploaded_file=pdf_galley_file,
            is_galley=True,
            label=label,
            save_to_disk=True,
            public=self.public_galley,
        )
        logger.debug(f"PDF galley {label} set onto {self.article.id}")
        return galley

    def run(self):
        # TODO: review me with specs#774: missing management of multilingual papers and PDF compilation
        # TODO: if targz: -> self.unpack_targz_from_jcomassistant()
        galleys_created = []
        try:
            self.path = self.unpack_zip_from_jcomassistant()
            self._check_conditions()
            galleys_created.extend((self.save_epub(), self.save_html(), self.save_pdf()))

        except Exception as e:
            # This logic is generally called asynchronously, so we don't
            # raise an exception here, but directly notify the typesetter.
            #
            # Errors should should have already been logged when they have happened.
            self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_FAILED
            self.article.articleworkflow.save()
            galleys_created = self.save_japrocessed_result()
            message_subject = "Galleys generation error"
            message_body = f"""Please check
<a href="{self.article.articleworkflow.url}">{self.article.id}</a>
<br><br>
Error:
<pre>
{e}
</pre>
"""
        else:
            self.article.articleworkflow.production_flag_galleys_ok = ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED
            self.article.articleworkflow.save()
            message_subject = "Galleys are ready"
            message_body = f"""Dear {self.request.user.full_name()},
<br><br>
Galleys for the {self.article.section.name} {self.article.pk} are ready.
<br>
Please go to the <a href="{self.article.articleworkflow.url}">web page</a>
"""

        communication_utils.notify_async_event(
            message_subject=message_subject,
            message_body=message_body,
            recipients=[self.request.user],
            article=self.article,
        )

        shutil.rmtree(self.path)
        return galleys_created

    def save_japrocessed_result(self) -> list[Galley]:
        """
        We save the given archive even if it has errors.

        We save it in the filesystem among the other article files and return it as a Galley object, so that
        our caller can process it easily (generally it will be linked to the TA or in the Article.galleys)
        """
        jcomassistant_response_content = File(
            BytesIO(self.archive_with_galleys),
            name="jcomassistant_response.tar.gz",
        )
        processed_archive_as_galley = save_galley(
            article=self.article,
            request=self.request,
            uploaded_file=jcomassistant_response_content,
            is_galley=False,
            label="JA processed",
            save_to_disk=True,
            public=self.public_galley,
        )
        # detach galley from article
        # refactor with TypesetterTestsGalleyGeneration?
        processed_archive_as_galley.article = None
        processed_archive_as_galley.save()

        # TODO: ensure that files on the filesystem are deleted/unlinked also!
        ta = self.article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
        ta.galleys_created.add(processed_archive_as_galley)
        return [processed_archive_as_galley]


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
        Clean existing galleys.

        This allows for several tests/uploads in the same typesetting round.
        """
        [g.unlink_files() for g in self.assignment.galleys_created.all()]
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
            public_galley=False,
        ).run()

    def _get_and_save_galleys(self):
        """
        Use Jcom Assistant to render the galleys and then attach them to the article.

        If settings.JCOMASSISTANT_MOCK_FILE is set, use the path as a mock response file instead of contacting
        the JCOM Assistant service.
        """
        if settings.JCOMASSISTANT_MOCK_FILE:
            galleys_created = self._mock_jcom_assistant_client(settings.JCOMASSISTANT_MOCK_FILE)
        else:
            response = self._jcom_assistant_client()
            galleys_created = AttachGalleys(
                archive_with_galleys=response.content,
                article=self.assignment.round.article,
                request=self.request,
                public_galley=False,
            ).run()
        # Detach the galleys from the article:
        # A.galley_set should contain only publication-ready galleys
        Galley.objects.filter(id__in=[g.id for g in galleys_created]).update(article=None)

        # Attach the gallyes to the TA
        self.assignment.galleys_created.set(galleys_created)

    def run(self) -> None:
        if self._check_conditions():
            self._clean_galleys()
            self._get_and_save_galleys()
        else:
            msg = f"""Galley generation failed to start
            article: {self.assignment.round.article.id}
            user: {self.request.user} is good: {self._check_user_conditions()}
            source files exist: {self._check_files_conditions()}
            """
            logger.error(msg.replace("\n", "  "))

            communication_utils.notify_async_event(
                message_subject="Galley generation failed to start",
                message_body=msg,
                recipients=[self.request.user],
                article=self.assignment.round.article,
            )


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
            article = self.archive_with_files_to_process.article
            communication_utils.notify_async_event(
                message_subject="Unexpected status code from jcomassistant",
                message_body=f"Please check JCOMAssistant service status.\n{response.content}\n{article.url}",
                recipients=[self.user],
                article=article,
            )
            logger.error(f"Unexpected status code {response.status_code} from jcomassistant for {article}.")
            raise ValueError(f"Unexpected status code {response.status_code}.")
        return response


@dataclasses.dataclass
class ReadyForPublication:
    """Bring a paper in RFP state."""

    workflow: ArticleWorkflow
    user: Account

    def _check_conditions(self) -> bool:
        """
        Check that the FSM allows the transaction.

        Take the operator into consideration.
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
        ta: TypesettingAssignment = self.workflow.get_latest_typesetting_assignment(only_completed=False)
        if is_article_author(self.workflow, self.user):
            self.workflow.author_deems_paper_ready_for_publication()
            if not ta.completed:
                logger.error(f"Programming error: TA {ta.id} should already result completed when author deems RFP!")
        elif is_article_typesetter(self.workflow, self.user):
            self.workflow.typesetter_deems_paper_ready_for_publication()
            if ta.completed:
                logger.error(f"Programming error: TA {ta.id} should not result completed when typesetter deems RFP!")
            ta.completed = timezone.now()
            ta.save()
        else:
            # should never be able to get here because _check_conditions is run berfore
            raise ValueError(f"Unexpected user attempting the transaction ({self.user=}). Possible programming error!")
        self.workflow.save()

        self.workflow.article.stage = STAGE_READY_FOR_PUBLICATION
        self.workflow.article.save()

    def _log_operation(self):
        """Add operation to timeline."""
        subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_production_complete",
            journal=self.workflow.article.journal,
        ).processed_value
        body = get_setting(
            setting_group_name="email",
            setting_name="production_complete",
            journal=self.workflow.article.journal,
        ).processed_value
        message = communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=subject,
            message_body=body,
            actor=self.user,
            recipients=[get_eo_user(self.workflow.article)],
            verbosity=Message.MessageVerbosity.TIMELINE,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
        return message

    def run(self):
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Paper not yet ready for publication. For assistance, contact the EO.")
            self._update_state()
            self._log_operation()
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
            flag_as_read=True,
            flag_as_read_by_eo=True,
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
        self.assignment = self.workflow.get_latest_typesetting_assignment(only_completed=True)
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

    def ensure_doi(self) -> None:
        """
        Ensure DOI exists.

        DOIs are generated by Janeway at acceptance.
        If our article does not yet have a DOI, log an error and create it.
        """
        if not Identifier.objects.filter(id_type="doi", article=self.workflow.article).exists():
            logger.error(f"No DOI for {self.workflow.article.id}. Please check article's history!")
            get_dois_for_articles(articles={self.workflow.article}, create=True)

    def set_article_identifiers(self):
        """Set DOI and pubid and publication date."""
        if not self.workflow.article.date_published:
            self.workflow.article.date_published = timezone.now()
            self.workflow.article.save()
        self.ensure_doi()
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
        """
        Include the given file into the article source files zip, under the given file-name.

        Defaults to replacing the tex source file (i.e. the file name will be something like JCOM_123.tex).
        """
        # TODO: talk with Elian on the opportunity of buildind a "texfile utils" library with similar functions
        # TODO: refactor with utils.guess_tex_filename()
        file_name = (
            f"{self.workflow.article.journal.code}_{self.workflow.article.id}.tex" if file_name is None else file_name
        )
        _tempfiledesc, tempfilename = tempfile.mkstemp(dir=self.source_files.parent)
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

        # Keep the files we have just modified in ArticleWorkflow.publication_galleys_source_file
        final_sources: JanewayFile = save_file_to_article(
            file_to_handle=File(open(tempfilename, "rb"), name=file_name.replace(".tex", ".zip")),
            article=self.workflow.article,
            owner=self.user,
            label="Final sources",
            description="Source files for final galleys",
            replace=None,
        )
        if self.workflow.publication_galleys_source_file:
            # Note that core.File.delete() also unlinks the related filesystem file
            self.workflow.publication_galleys_source_file.delete()
        self.workflow.publication_galleys_source_file = final_sources
        self.workflow.save()
        os.unlink(tempfilename)

    def _prepare_source(self, source_file: BytesIO) -> BytesIO:
        r"""
        Set pubid, DOI and publication date into the given file and return it.

        Placeholders are expected as follow:
        \publicationData{23}{06}{A}{02}
        \publicationDoi{10.22323/2.23060202}

        Raises:
          ValueError: if expected macros cannot be found in the given file.

        """
        article = self.workflow.article
        volume = f"{article.primary_issue.volume:02d}"
        # TODO: can it ever happen that issue.issue is not in the form "01"?
        issue = f"{int(article.primary_issue.issue):02d}"
        # Page numbers should have been set when we set the pubid when we do set_article_identifiers()
        # ATM, they have the form "A01", "Y02", ... (see AW.compute_eid())
        # in the TeX source we need them separately: the type "A", "Y"... and the counter "01", "02"...
        type_and_counter = article.page_numbers
        counter = type_and_counter[-2:]
        type_code = type_and_counter[:1]
        doi = article.get_doi()
        if not doi:
            raise ValueError(f"DOI for {article.id} shold already exist at begin-publication!")
        # Please keep coherent with conftest.jcom_automatic_preamble for documentation.
        replacements = (
            # f-strings and latex macros don't dance well together...
            (
                rf"\publicationData{{00}}{{00}}{{{type_code}}}{{00}}",
                rf"\publicationData{{{volume}}}{{{issue}}}{{{type_code}}}{{{counter}}}",
            ),
            (r"\publicationDoi{10.22323/0.00000000}", rf"\publicationDoi{{{doi}}}"),
        )

        source_file.seek(0)
        # we can safely assume that we are dealing with a utf8-encoded text file
        content = source_file.read().decode("utf-8")

        # I expect to always find all the place-holders
        for old_string, new_string in replacements:
            if old_string in content:
                content = content.replace(old_string, new_string, 1)
            else:
                raise ValueError(f"""Missing macro "{old_string}" in Automatic Preamble of {article.id}""")
        return BytesIO(content.encode("utf-8"))

    def _get_source_file(self) -> BytesIO:
        """
        Extract the source file of the article galleys.

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
        async_task(
            finishpublication_wrapper,
            workflow_pk=self.workflow.pk,
            user_pk=self.user.pk,
            task_name="finish-publication__automatic-trigger",
        )

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
            archive_with_files_to_process=self.workflow.publication_galleys_source_file,
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
            public_galley=True,
        ).run()
        self.workflow.article.galley_set.set(galleys_created)

        # The "galleys-ok" flag is set by AttachGalleys that we have just run
        if self.workflow.production_flag_galleys_ok == ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED:
            if html_galleys := [g for g in galleys_created if g.label == "HTML"]:
                self.workflow.article.render_galley = html_galleys[0]
                self.workflow.article.save()
            return True
        else:
            communication_utils.notify_async_event(
                message_subject="Final galley generation failed",
                message_body=f"""Please note that the generation has been attempted on the article sources,
and this have been automatically derived from the latest typesetted files.

This is usually related to some temporary issue with the infrastructure.

Please retry and contact assistance is the problem persists.

{self.workflow.url}
""",
                recipients=[self.request.user],
                article=self.workflow.article,
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
        self._trigger_on_article_published_event()

    def _trigger_workflow_event(self):
        """Trigger the ON_WORKFLOW_ELEMENT_COMPLETE event to comply with upstream review workflow."""
        workflow_kwargs = {
            "handshake_url": "wjs_review_list",
            "request": self.request,
            "article": self.workflow.article,
            # Stage is already set to STAGE_PUBLISHED by import_utils.publish_article
            "switch_stage": False,
        }
        events_logic.Events.raise_event(
            events_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE,
            task_object=self.workflow.article,
            **workflow_kwargs,
        )

    def _trigger_on_article_published_event(self):
        """
        Trigger ON_ARTICLE_PUBLISHED event.
        """
        kwargs = {
            "article": self.workflow.article,
            "request": self.request,
        }
        events_logic.Events.raise_event(
            events_logic.Events.ON_ARTICLE_PUBLISHED, task_object=self.workflow.article, **kwargs
        )

    def _get_context(self):
        """Return a context suitable to render the notification message."""
        return {
            "article": self.workflow.article,
        }

    def _log_operation(self, context) -> Message:
        """Log the operation."""
        message_subject = get_setting(
            setting_group_name="email_subject",
            setting_name="subject_author_publication",
            journal=self.workflow.article.journal,
        ).processed_value
        message_body = render_template_from_setting(
            setting_group_name="email",
            setting_name="author_publication",
            journal=self.workflow.article.journal,
            request=self.request,
            context=context,
            template_is_setting=True,
        )
        message = communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[
                self.workflow.article.correspondence_author,
            ],
            verbosity=Message.MessageVerbosity.FULL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
        return message

    def run(self):
        with transaction.atomic():
            green_light, reason = self.check_conditions()
            if not green_light:
                raise ValueError(f"Final falley generation cannot be started. {reason}")
            if self.generate_final_galleys():
                self.update_state()
                self._log_operation(self._get_context())
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


def reunite_divided_kwds(kwds: QuerySet[Keyword]) -> tuple[list[int], list[int]]:
    """
    Given some kwds, return the correct ones.

    If any permutation of the given kwds exists as a whole kwd,
    consider the member of the permutation as "bad" kwds, and the combined one as a "good" one.

    Raises:
      ValueError: if we find more than one kwd with a certain "combined-word" in the DB.

    """
    good: list[int] = []
    bad: list[int] = []
    unprocessed_kwds = list(kwds.values_list("id", "word"))  # e.g. [(12, 'a, b'), (13, 'a'), ...
    r = len(unprocessed_kwds)
    while True:
        if r < 2:
            break
        if len(unprocessed_kwds) < 2:
            break
        for permutation in permutations(unprocessed_kwds, r):
            joined_word = ", ".join([k[1] for k in permutation])
            kwds_for_joined_word = Keyword.objects.filter(word=joined_word)
            if kwds_for_joined_word.count() > 1:
                msg = f"{kwds_for_joined_word.count()} matches for {joined_word}. Expected max 1!"
                raise ValueError(msg)
            if kwds_for_joined_word.count() == 1:
                good.append(kwds_for_joined_word.values_list("id", flat=True).get())
                for k in permutation:
                    bad.append(k[0])
                    unprocessed_kwds.remove(k)
                r -= 1
                break  # we can safely assume that if "a, b" exists, then "b, a" does not
        r -= 1
    good.extend([k[0] for k in unprocessed_kwds])
    return (good, bad)


@dataclasses.dataclass
class MetadataFromTeX:
    """
    Extract metadata from TeX.

    Send the latest typesetted tex source file to jcomassistant and
    receive the metadata extracted from that.

    Enrich returned metadata with a mapping of the kwds strings to DB objects.

    """

    workflow: ArticleWorkflow
    _data: dict[str:Any] = dataclasses.field(init=False)

    # TODO: refactor with data_extractor.Author (or drop?)
    @dataclass
    class SimilarAccount:
        """Convenience."""

        pk: int
        last_name: str
        first_name: str
        email: str
        orcid: str | None
        country: str | None
        institution: str | None
        biography: str | None
        link_to_mapping: str | None

    @dataclass
    class AuthorStruct:
        """Convenience."""

        last_name: str
        first_name: str
        email: str
        orcid: str | None
        extra_email: str | None
        account_id: int | None
        warning: str | None
        similar_accounts: QuerySet[Account] | None = None
        must_be_created: bool = False

    # TODO: allow for type-hint TexData without depending from jcomassistant
    def get_raw_data(self) -> dict:
        """
        Ask Jcomassistant to extract structured metadata from TeX.

        Raises:
          HTTPError: if jcomassistant returned something different that 200

        """
        # Use the source file from the latest typesetting round.  We probably will never change these data after
        # publication (when the source file is AW.publication_galleys_source_file), but even if we do, title and
        # abstract should be the same in the two source files.
        tex_source_file = self._get_source_file()
        files = {"file": tex_source_file}
        url = settings.JCOMASSISTANT_URL.replace("jcomassistant/", "texdata")
        response = requests.post(url=url, files=files, timeout=10)
        if response.status_code != 200:
            msg = f"Unexpected status code {response.status_code} for {url}"
            raise requests.exceptions.HTTPError(msg)
        return response.json()

    def get_data(self) -> dict:
        """Adapt and enrich TeX data."""
        self._data = self.get_raw_data()

        if self._data.get("language") != self.workflow.article.language:
            msg = (
                f"DB language: '{self.workflow.article.language}', TeX language: '{self._data.get('language')}' - "
                " Please check!"
            )
            raise ValueError(msg)

        # Adapt abstract (see also wjs/specs#1773)
        self._data["abstract_adapted"] = re.sub(r"\n", " ", self._data.get("abstract"))
        self._data["abstract_adapted"] = re.sub(r"  +", " ", self._data["abstract_adapted"])

        # Map kwds data from TeX to DB objects
        self._data["kwds_tex"] = self._get_tex_kwds()
        # These two can be dropped after wjs/wjs-help#44 is solved
        self._data["kwds_db"] = self._get_db_kwds()
        self._data["kwds_db_raw"] = self.workflow.article.keywords.all()

        # Map authors and find eventual related errors
        self._data["authors_map"] = self._map_authors()
        self._data["authors_errors"] = self._find_authors_errors()

        return self._data

    def update_titleabstract(self):
        """Retrieve data and use it to update DB title and abstract."""
        self.get_data()

        # NB: we use the "adapted" abstract
        # because it's the closer to the result of a manual edit from the manager interface

        # the title and abstract of the tex are saved in the correspondent translation of the article
        lang = pycountry.languages.get(alpha_3=self.workflow.article.language.upper()).alpha_2
        with translation.override(lang):
            self.workflow.article.title = self._data.get("title")
            self.workflow.article.abstract = self._data.get("abstract_adapted")

        self.workflow.article.save()

    def update_keywords(self):
        """Retrieve data and use it to update DB keywords."""
        self.get_data()

        article = self.workflow.article
        # Using self.workflow.keywords.set(self._data["kwds_tex"]) gives
        # create_m2m_ordered_through_manager.<locals>.M2MOrderedThroughManager.add() got
        #   an unexpected keyword argument 'through_defaults'
        # So I fallback to the following one-by-one approach:
        article.keywords.clear()
        for order, kwd in enumerate(self._data["kwds_tex"]):
            KeywordArticle.objects.update_or_create(
                article=article,
                keyword=kwd,
                defaults={"order": order},
            )

    def update_authors(self):
        """
        Retrieve data and use it to update DB authors; also freeze authors.

        Raises:
          ValueError: if called while any authors-errors exist.

        """
        self.get_data()

        if self._data["authors_errors"]:
            raise ValueError(self._data["authors_errors"])

        # 🤔 article.authors.clear() does not delete records in the "through" table!?
        self.workflow.article.authors.clear()
        ArticleAuthorOrder.objects.filter(article=self.workflow.article).delete()
        for order, am in enumerate(self._data["authors_map"]):
            if am.must_be_created:
                author = Account.objects.create(
                    first_name=am.first_name,
                    last_name=am.last_name,
                    email=am.email,
                    orcid=am.orcid,
                )
                self._log_new_coauthor_created(author)
            else:
                author = Account.objects.get(
                    id=am.account_id,
                )
                # FIXME: update with data from DB:
                # - first/last name (only if longer than DB)
                # - email (only add to jcom_profile.Correspondence)
                # - orcid
                # https://gitlab.sissamedialab.it/wjs/specs/-/issues/1804

            # ???? why do I need both authors.add(author) and AAO.create(author...) ????
            self.workflow.article.authors.add(author)
            ArticleAuthorOrder.objects.create(article=self.workflow.article, order=order, author=author)

        sync_frozen_authors_with_authors(self.workflow.article)

    # TODO: refactor with logic__production.BeginPublication._get_source_file()
    def _get_source_file(self) -> BytesIO:
        """
        Extract the source file of the article galleys.

        Use the latest available files:
        if the latest production version (typesetting-assignment) has just started,
        the files are taken from the previous version.

        Return the main TeX file.

        Raises:
          FileNotFoundError:
          - if we cannot find the zip source.
          - if we cannot find the main tex file in the zip source.

        """
        ta = (
            TypesettingAssignment.objects.filter(
                round__article=self.workflow.article,
                files_to_typeset__isnull=False,
            )
            .order_by("-round__round_number")
            .first()
        )
        zip_source_file = ta.files_to_typeset.first()
        if not zip_source_file:
            msg = _("No source files. Please upload some!")
            raise FileNotFoundError(msg)
        zip_source_file = zip_source_file.self_article_path()

        tex_source_name = f"{self.workflow.article.journal.code}_{self.workflow.article.id}.tex"

        with zipfile.ZipFile(zip_source_file) as zip_file:
            if tex_source_name in zip_file.namelist():
                main_tex = zip_file.open(tex_source_name)
            else:
                msg = f"Cannot read {tex_source_name} from {zip_source_file} for {self.workflow.article.id}"
                raise FileNotFoundError(msg)
        return main_tex

    def _get_db_kwds(self) -> QuerySet[Keyword]:
        """
        Get the "real" article kwds.

        Deal with the case when the kwds on the DB have been erroneously split by Janeway manger UI (it splits
        a kwd on the ",", so that a single kwd "aaa, bbb" becomes two kwds: "aaa" and "bbb").

        """
        # Note that kwds are implicitly ordered because of core.models_utils.M2MOrderedThroughField, however, when we
        # "reunite" them, we get back a list of ids of kwds that might not even be linked to the article. So we must
        # ensure that the order is "maintained".
        good, _ = reunite_divided_kwds(self.workflow.article.keywords.all())
        order_of_ids = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(good)])
        return Keyword.objects.filter(id__in=good).order_by(order_of_ids)

    def _get_tex_kwds(self) -> QuerySet[Keyword]:
        """
        Get the kwds that exist in the TeX file.

        Raises:
          ValueError: if any kwd from the TeX does not exists in the DB.

        """
        tex_kwds_strings = self._data.get("keywords")

        # Expect kwds to be in the article's language, so we need to compare the received string with the
        # appropriate translation.
        tex_kwds = Keyword.objects.none()
        lang = pycountry.languages.get(alpha_3=self.workflow.article.language).alpha_2
        # Apparently `annotate` is not patched by django-modeltranslation
        # https://django-modeltranslation.readthedocs.io/en/latest/usage.html#multilingual-manager-1
        # so we have to manually select the correct field to use:
        lang_field = f"word_{lang}"
        with translation.override(lang):
            tex_kwds = (
                Keyword.objects.filter(
                    journal=self.workflow.article.journal,
                    **{f"{lang_field}__in": tex_kwds_strings},
                )
                # We need to manully order the queryset to maintain the order we found in the TeX
                .annotate(
                    manual_order=Case(
                        *[When(**{lang_field: word}, then=pos) for pos, word in enumerate(tex_kwds_strings)],
                        output_field=IntegerField(),
                    ),
                ).order_by("manual_order")
            )

        if len(tex_kwds_strings) != tex_kwds.count():
            tex_kwds_indb = set(tex_kwds.values_list("word", flat=True))
            msg = ""
            if only_tex := set(tex_kwds_strings) - tex_kwds_indb:
                msg += f" Kwds from TeX that do not exist in the DB: {'; '.join(only_tex)}."
            if only_db := tex_kwds_indb - set(tex_kwds_strings):
                msg += f" 😱 This cannot be! Only in DB: {'; '.join(only_db)}."
            msg += " Please contact assistance!"
            raise ValueError(msg)
        return tex_kwds

    def _map_authors(self) -> list["AuthorStruct"]:
        """
        Use TeX data to retrieve Accounts from DB.

        When no suitable account can be found, data suitable to create a new account is also prepared.
        """
        article = self.workflow.article
        subq = Subquery(
            ArticleAuthorOrder.objects.filter(
                article=article,
                author__id=OuterRef("id"),
            ).values_list("order"),
        )
        db_authors = article.authors.all().annotate(order=subq).order_by("order")

        # 🤔 ugly code: side effect in a method that returns something...
        self._data["authors_db"] = db_authors

        tex_authors = self._data.get("authors_data")
        authors_map = []
        for tex_author in tex_authors:
            accountstruct = self._find_corresponding_account(tex_author)
            authors_map.append(accountstruct)
        return authors_map

    def _find_corresponding_account(self, tex_author: dict) -> "AuthorStruct":
        """
        Find an Account in the DB, given some author data.

        Try to find the account in many ways:
        - by email
        - by orcid (if available)
        - by old wjapp correspondence / mapping using the email (aka via extra_email)
        - by first + last on article.authors
        - by first-initial + last on article.authors
        - by first + last on all DB (select first and add a "warning" if >1)
        - by first-initial + last on all DB (select first and add a "warning" if >1)
        - give up and propose to add a new Account
          - notify the author of the new account

        If no account in the DB can be found, an Account() object populated with data from the TeX is returned.
        """
        try:
            account = Account.objects.get(email=tex_author["email"])
        except Account.DoesNotExist:
            pass
        else:
            return MetadataFromTeX.AuthorStruct(
                last_name=account.last_name,
                first_name=account.first_name,
                email=account.email,
                orcid=account.orcid,
                extra_email=None,
                account_id=account.id,
                must_be_created=False,
                warning=None,
            )

        if tex_author["orcid"]:
            try:
                account = Account.objects.get(orcid=tex_author["orcid"])
            except Account.DoesNotExist:
                pass
            else:
                return MetadataFromTeX.AuthorStruct(
                    last_name=account.last_name,
                    first_name=account.first_name,
                    email=account.email,
                    orcid=account.orcid,
                    extra_email=None,  # FIXME!
                    account_id=account.id,
                    must_be_created=False,
                    warning=None,
                )

        try:
            wjapp_mapping = Correspondence.objects.get(email=tex_author["email"])
        except Correspondence.DoesNotExist:
            pass
        else:
            return MetadataFromTeX.AuthorStruct(
                last_name=wjapp_mapping.account.last_name,
                first_name=wjapp_mapping.account.first_name,
                email=wjapp_mapping.account.email,
                orcid=wjapp_mapping.account.orcid,
                extra_email=None,  # FIXME!
                account_id=wjapp_mapping.account.id,
                must_be_created=False,
                warning=None,
            )

        similaraccounts_warning = """Similar accounts: either set the orcid on the existing account (if the TeX has
        it), or create/edit a "correspondence" (a mapping) with the TeX email."""
        try:
            wjapp_mapping = self.workflow.article.authors.get(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = self.workflow.article.authors.filter(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
            return MetadataFromTeX.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        try:
            wjapp_mapping = self.workflow.article.authors.get(
                last_name=tex_author["surname"],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = self.workflow.article.authors.filter(
                last_name=tex_author["surname"],
            )
            return MetadataFromTeX.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        try:
            wjapp_mapping = Account.objects.get(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = Account.objects.filter(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
            return MetadataFromTeX.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        try:
            wjapp_mapping = Account.objects.get(
                first_name__startswith=tex_author["first_name"][0],
                last_name__endswith=tex_author["surname"].split(" ")[-1],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = Account.objects.filter(
                first_name__startswith=tex_author["first_name"][0],
                last_name__endswith=tex_author["surname"].split(" ")[-1],
            )
            return MetadataFromTeX.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        return MetadataFromTeX.AuthorStruct(
            last_name=tex_author["surname"],
            first_name=tex_author["first_name"],
            email=tex_author["email"],
            orcid=tex_author["orcid"],
            extra_email=None,
            account_id=None,
            must_be_created=True,
            warning=None,
        )

    def _find_authors_errors(self) -> list[str]:
        """
        Tell if there is some unresolvable error in the authors list.

        ATM only check that owner and corresondence author are in the list of mapped authors.
        """
        errors = []
        proposed_authors_ids = [a.account_id for a in self._data["authors_map"] if a.account_id]
        if self.workflow.article.owner.id not in proposed_authors_ids:
            errors.append(_("No owner in the new authors list!"))
        if self.workflow.article.correspondence_author.id not in proposed_authors_ids:
            errors.append(_("No correspondence author in the new authors list!"))
        if any(am.similar_accounts for am in self._data["authors_map"]):
            errors.append(_("Similar accounts exist!"))
        return errors

    def _enrich_similar_accounts(self, tex_author: dict, similar_accounts: QuerySet[Account]) -> list[SimilarAccount]:
        """
        Enrich the list of similar accounts by computing the appropriate correspondence URL.

        Since it's possible to have existing mappings/correspondences with empty email, the idea here is to give the
        operator a direct link to the most appropriate action: edit the exising correspondence if the email is missing
        or add a new correspondence.

        """
        result = []
        for i, account in enumerate(similar_accounts):
            if not Correspondence.objects.filter(account=account, email__isnull=True).exists():
                link_to_mapping = reverse("admin:jcom_profile_correspondence_add")
                querystring = urlencode(
                    {
                        "account": account.pk,
                        "email": tex_author["email"],
                        # "source" and "user_cod" are mandatory for a Correspondence
                        # below we make-up suitable values:
                        "source": "tex",
                        "user_cod": self.workflow.article.pk * 100 + i,
                    },
                )
            else:
                correspondence = Correspondence.objects.filter(account=account, email__isnull=True).first()

                link_to_mapping = reverse("admin:jcom_profile_correspondence_change", args=[correspondence.pk])
                querystring = urlencode(
                    {
                        "email": tex_author["email"],
                        "source": "tex",
                        "user_cod": self.workflow.article.pk * 100 + i,
                    },
                )
            link_to_mapping = f"{link_to_mapping}?{querystring}"

            result.append(
                MetadataFromTeX.SimilarAccount(
                    pk=account.pk,
                    last_name=account.last_name,
                    first_name=account.first_name,
                    email=account.email,
                    orcid=account.orcid,
                    country=account.country.name if account.country else "",
                    institution=account.institution,
                    biography=account.biography,
                    link_to_mapping=link_to_mapping,
                ),
            )
        return result

    def _log_new_coauthor_created(self, newaccount: Account) -> Message:
        """Send a notification to the newly-created account."""
        fake_request = create_fake_request(
            user=get_eo_user(self.workflow.article.journal),
            journal=self.workflow.article.journal,
        )
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="add_coauthor_manually_subject",
            journal=self.workflow.article.journal,
            request=fake_request,
            context={"article": self.workflow.article},
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="add_coauthor_manually_body",
            journal=self.workflow.article.journal,
            request=fake_request,
            context={"article": self.workflow.article, "newaccount": newaccount},
            template_is_setting=True,
        )
        return communication_utils.log_operation(
            article=self.workflow.article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[newaccount],
            verbosity=Message.MessageVerbosity.EMAIL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
