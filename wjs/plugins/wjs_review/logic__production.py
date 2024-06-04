"""Logic classes for production-related actions & co.

This module should be *-imported into logic.py
"""
import dataclasses
import datetime
import os
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, Union
from zipfile import ZipFile

import lxml.html
import requests
from core.files import save_file_to_article
from core.models import File as JanewayFile
from core.models import Galley, SupplementaryFile
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.module_loading import import_string
from django_fsm import can_proceed
from events import logic as events_logic
from lxml.html import HtmlElement
from plugins.typesetting.models import (
    GalleyProofing,
    TypesettingAssignment,
    TypesettingRound,
)
from production.logic import save_galley, save_galley_image
from submission import models as submission_models
from submission.models import STAGE_PROOFING, STAGE_TYPESETTING, Article
from utils.logger import get_logger
from utils.setting_handler import get_setting

from wjs.jcom_profile.import_utils import (
    decide_galley_label,
    evince_language_from_filename_and_article,
    process_body,
)
from wjs.jcom_profile.utils import render_template_from_setting

from . import communication_utils
from .models import ArticleWorkflow, Message
from .permissions import (
    has_typesetter_role_by_article,
    is_article_author,
    is_article_typesetter,
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
            recipients=[
                self.typesetter,
            ],
            message_type=Message.MessageTypes.SYSTEM,
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

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.assignment.round.article.articleworkflow, self.request.user)

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
            if not self._check_typesetter_condition():
                raise ValueError("Invalid state transition")
            # Check if there are any files already associated
            if self.assignment.files_to_typeset.exists():
                self._delete_core_files_record()
                self._remove_file_from_assignment()

            uploaded_file = save_file_to_article(self.file_to_upload, self.assignment.round.article, self.typesetter)
            self._update_typesetting_assignment(uploaded_file)
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


@dataclasses.dataclass
class HandleCreateSupplementaryFile:
    """Handle the creation and upload of supplementary files."""

    request: HttpRequest
    article: Article

    def _create_file_instance(self):
        file_instance = save_file_to_article(self.request.FILES["file"], self.article, self.request.user)
        return file_instance

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.article.articleworkflow, self.request.user)

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

    request: HttpRequest
    supplementary_file: SupplementaryFile
    article: Article

    def _check_typesetter_condition(self):
        return is_article_typesetter(self.article.articleworkflow, self.request.user)

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
            setting_group_name="wjs_review",
            setting_name="author_sends_corrections_subject",
            journal=self.article.journal,
        ).processed_value
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
            message_type=Message.MessageTypes.SYSTEM,
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

    path: Path
    article: Article
    request: HttpRequest

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
        """Set the give file as HTML galley."""
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
        """Set the give file as EPUB galley."""
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
        pass

    def run(self):
        galleys_created = []
        galleys_created.append(self.save_epub())
        galleys_created.append(self.save_html())
        return galleys_created


@dataclasses.dataclass
class TypesetterTestsGalleyGeneration:
    """Generate galleys for an article."""

    assignment: TypesettingAssignment
    request: HttpRequest  # Used only Janeway's save_galley, in log_operation and maybe in _check_conditions

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
            source_archive=self.assignment.files_to_typeset.first().self_article_path(),
        )
        galleys_directory = assistant.ask_jcomassistant_to_process()
        return galleys_directory

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
            message_type=Message.MessageTypes.SYSTEM,
        )
        return message

    def _generate_and_attach_galleys(self):
        """Generate galleys.

        - send source files to jcomassistant
        - attach generated galleys to TA (and article)
        - record success/failure of the generation
        """
        galleys_created = AttachGalleys(
            path=self._jcom_assistant_client(),
            article=self.assignment.round.article,
            request=self.request,
        ).run()
        self.assignment.galleys_created.set(galleys_created)
        self._record_success_of_galley_generation()

    def _record_success_of_galley_generation(self):
        """Fixme.

        This is a stub. ATM, blindly record the galley-generation as a succes.

        TODO: fixme in wjs-profile-project#105
        """
        self.assignment.round.article.articleworkflow.production_flag_galleys_ok = True
        self.assignment.round.article.articleworkflow.save()
        logger.warning(f"{self.assignment.round.article.articleworkflow.production_flag_galleys_ok=}")

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
            raise ValueError("Cannot generate Galleys. Please contact the editorial office.")
        self._clean_galleys()
        self._generate_and_attach_galleys()
        self._check_queries_in_tex_src()
        context = self._get_message_context()
        self._log_operation(context)


@dataclasses.dataclass
class JcomAssistantClient:
    """Client for JCOM Assistant."""

    source_archive: Union[str, Path]
    workdir: Union[str, Path] = None

    def ask_jcomassistant_to_process(self) -> Path:
        """Send the given zip file to jcomassistant for processing.

        Return the path to a folder with the unpacked response.
        """
        url = settings.JCOMASSISTANT_URL
        logger.debug(f"Contacting jcomassistant service at {url}...")
        files = {"file": open(self.source_archive, "rb")}

        response = requests.post(url=url, files=files)
        if response.status_code != 200:
            logger.error(
                "Unexpected status code {response.status_code} processing {source_archive}. Trying to proceed..."
            )
        return self.unpack_targz_from_jcomassistant(response.content)

    def unpack_targz_from_jcomassistant(
        self,
        galleys_archive: bytes,
    ):
        """Unpack an archive received from jcomassistant.

        Accept the archive in the form of a bytes string.

        If a workdir is provided, unpack it in a new folder there,
        otherwise create and use a temporary folder.
        The caller should clean up if necessary.
        """
        unpack_dir = tempfile.mkdtemp(dir=self.workdir)
        # Use BytesIO to treat bytes data as a file
        with BytesIO(galleys_archive) as file_obj:
            # Open the tar.gz archive
            with tarfile.open(fileobj=file_obj, mode="r:gz") as tar:
                # Extract all contents into the unpack directory
                tar.extractall(path=unpack_dir)
        unpack_dir = Path(unpack_dir)
        self.reemit_info_and_up(unpack_dir=unpack_dir)

        logger.debug(f"...jcomassistant processed files are in {unpack_dir}.")
        return unpack_dir

    def reemit_info_and_up(self, unpack_dir: Path) -> None:
        """Emit as log messages lines read from the given log file.

        Expect the logfile to contain log-formatted lines suchs as:
        DEBUG From: ...

        Re-emit only info, wraning, error and critical.
        """
        # log files are called something like
        # - galley-xxx.epub_log
        # - galley-xxx.html_log
        # - galley-xxx.srvc_log
        # We are going to use only the service log (*.srvc_log)
        srvc_log_files = list(unpack_dir.glob("galley-*.srvc_log"))
        if len(srvc_log_files) != 1:
            logger.warning(f"Found {len(srvc_log_files)} srvc_log files. Ask Elia")
        srvc_log_file = srvc_log_files[0]
        with open(srvc_log_file) as log_file:
            for line in log_file:
                if line.startswith("INFO"):
                    logger.info(f"JA {line[11:-1]}")
                elif line.startswith("WARNING"):
                    logger.warning(f"JA {line[14:-1]}")
                elif line.startswith("ERROR"):
                    logger.error(f"JA {line[12:-1]}")
                elif line.startswith("CRITICAL"):
                    logger.critical(f"JA {line[15:-1]}")
                elif line.startswith("DEBUG"):
                    logger.debug(f"JA {line[12:-1]}")


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

        self.workflow.article.stage = submission_models.STAGE_READY_FOR_PUBLICATION
        self.workflow.article.save()

    def run(self):
        with transaction.atomic():
            if not self._check_conditions():
                raise ValueError("Paper not yet ready for publication. For assitance, contact the EO.")
            self._update_state()
        return self.workflow
