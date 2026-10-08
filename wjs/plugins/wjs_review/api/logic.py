"""Business logic classes serving the wjs_review API.

Only logic that has no counterpart in the server-rendered views lives here; anything shared
with them belongs in the plugin's ``logic*.py`` modules and is imported from there.
"""

import dataclasses
import zipfile
from pathlib import Path, PurePosixPath

from core import files
from core.models import File as JanewayFile
from core.models import Galley
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import HttpRequest
from submission.models import STAGE_PUBLISHED
from typesetting.models import GalleyProofing, TypesettingAssignment
from utils.logger import get_logger
from utils.setting_handler import get_setting

from ..events.handlers import clear_cache
from ..logic__production import AttachGalleys, JcomAssistantClient
from ..models import ArticleWorkflow
from ..utils import guess_typesetted_texfile_name

logger = get_logger(__name__)
Account = get_user_model()

#: `ValidationError` code marking a failure caused by what the caller sent, as opposed to one of
#: the generation itself. The API answers the former with a "400", the latter with a "502".
INVALID_REQUEST_CODE = "invalid_request"


@dataclasses.dataclass
class ReplaceSourceZipAndRegenerateGalleys:
    """Replace a published article's galley sources and rebuild every galley from them.

    This is the API counterpart of the second half of the publication process: it reuses
    :class:`~..logic__production.JcomAssistantClient` and
    :class:`~..logic__production.AttachGalleys` exactly as
    :class:`~..logic__production.FinishPublication` does, but it neither touches the FSM state
    nor injects identifiers into the TeX sources — the archive is stored as it is uploaded,
    since the client edits the archive that this same entry point served it.
    """

    workflow: ArticleWorkflow
    source_zip: UploadedFile
    "The archive uploaded by the client, replacing ``ArticleWorkflow.publication_galleys_source_file``."

    user: Account
    "Who is asking; owns the stored file and is notified when galley generation goes wrong."

    request: HttpRequest
    "Janeway's ``save_galley()``/``save_galley_image()`` need a request object."

    expected_galleys: list[str] = dataclasses.field(init=False)
    "The galleys that this journal expects to be generated."

    def __post_init__(self):
        """Find out which galleys we should expect to find in the jcomassistant response."""
        self.expected_galleys = get_setting(
            setting_group_name="wjs_review",
            setting_name="expected_galleys",
            journal=self.workflow.article.journal,
        ).processed_value

    def check_conditions(self) -> tuple[bool, str | None]:
        """
        Say whether the article's galleys may be rebuilt from the given archive.

        :return: whether to go ahead and, when not, what to tell the caller.
        :rtype: tuple
        """
        if self.workflow.article.stage != STAGE_PUBLISHED:
            return (False, "Article is not published.")
        if not self.expected_galleys:
            return (False, "No galley is expected for this journal: nothing to generate.")
        return self.check_archive_belongs_to_article()

    def check_archive_belongs_to_article(self) -> tuple[bool, str | None]:
        """
        Say whether the archive is the one of the article it is being uploaded onto.

        Nothing else ties the two together: the archive arrives as a bare request body, so a typo
        in the article id of the URL would have us rebuild one article's galleys out of another
        article's sources - and publish them. The TeX source is named after the article it
        belongs to ("JCOM_3676.tex"), so finding it in the archive is that missing tie.

        It is looked up at the root of the archive, where the rest of the production pipeline
        expects it too (see `BeginPublication._get_source_file`).

        :return: whether the archive carries this article's TeX source and, when it does not, what
            is wrong with it.
        :rtype: tuple
        """
        expected_tex_name = guess_typesetted_texfile_name(self.workflow.article)
        try:
            with zipfile.ZipFile(self.source_zip) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile as exception:
            return (False, f"The archive cannot be read. {exception}")
        finally:
            # Whoever stores the archive next reads it from the beginning.
            self.source_zip.seek(0)

        if expected_tex_name in names:
            return (True, None)

        if nested := [name for name in names if PurePosixPath(name).name == expected_tex_name]:
            return (
                False,
                f"The archive contains {nested[0]}, but {expected_tex_name} is expected at its root:"
                " please archive the source files themselves, not the folder containing them.",
            )
        return (
            False,
            f"The archive does not contain {expected_tex_name}, so it does not look like the sources"
            f" of {self.workflow.preprint_id}. Please check that the article id is the right one.",
        )

    def store_source_zip(self) -> JanewayFile:
        """
        Store the uploaded archive as the article's publication-galleys sources.

        When the article already has such an archive, the new one overwrites it in place:
        this keeps the same ``core.File`` row (and Janeway's file history, i.e. the previous
        archive is not lost) so that a failed generation can be rolled back cleanly.

        :return: the stored file.
        :rtype: JanewayFile
        """
        article = self.workflow.article
        # Name the file as the publication process does, so that both look alike among the article's files.
        self.source_zip.name = f"{article.journal.code}_{article.pk}.zip"

        if current_source := self.workflow.publication_galleys_source_file:
            return files.overwrite_file(
                uploaded_file=self.source_zip,
                file_to_replace=current_source,
                path_parts=("articles", article.pk),
            )

        new_source = files.save_file_to_article(
            file_to_handle=self.source_zip,
            article=article,
            owner=self.user,
            label="Final sources",
            description="Source files for final galleys",
            replace=None,
        )
        self.workflow.publication_galleys_source_file = new_source
        self.workflow.save()
        return new_source

    def generate_galleys(self) -> list[Galley]:
        """
        Ask jcomassistant to process the stored sources and turn its answer into galleys.

        :return: the galleys built from the uploaded archive.
        :rtype: list[Galley]

        :raises ValidationError: if jcomassistant cannot be reached / refuses the archive, or if
            the generated files could not be attached to the article.
        """
        try:
            response = JcomAssistantClient(
                archive_with_files_to_process=self.workflow.publication_galleys_source_file,
                user=self.user,
                galleys_to_request=self.expected_galleys,
            ).ask_jcomassistant_to_process()
        except (ValueError, NotImplementedError) as exception:
            raise ValidationError(f"Galley generation failed. {exception}")

        # AttachGalleys is normally called asynchronously, so it does not raise: it reports what
        # happened through the workflow's galleys flag (and notifies `request.user` either way).
        galleys = AttachGalleys(
            archive_with_galleys=response.content,
            article=self.workflow.article,
            request=self.request,
            public_galley=True,
            expected_galleys=self.expected_galleys,
        ).run()
        self.workflow.refresh_from_db(fields=["production_flag_galleys_ok"])
        if self.workflow.production_flag_galleys_ok != ArticleWorkflow.GalleysStatus.TEST_SUCCEEDED:
            raise ValidationError("Galley generation failed. Please check the article's sources.")
        return galleys

    def attach_galleys(self, galleys: list[Galley]):
        """
        Make the given galleys the only ones of the article, and get rid of the ones they replace.

        :param galleys: the galleys just generated.
        :type galleys: list[Galley]
        """
        article = self.workflow.article
        replaced = [galley for galley in article.galley_set.all() if galley not in galleys]
        article.galley_set.set(galleys)
        if html_galleys := [galley for galley in galleys if galley.label == "HTML"]:
            article.render_galley = html_galleys[0]
        elif article.render_galley and article.render_galley not in galleys:
            # Do not leave the article rendering a galley that is not attached to it any more.
            logger.warning(f"No HTML galley regenerated for {article.pk}: dropping its render galley.")
            article.render_galley = None
        article.save()
        self.discard_galleys(replaced)

    def discard_galleys(self, galleys: list[Galley]):
        """
        Delete the galleys that the new ones replace, with the files they are made of.

        Replacing an article's galleys only detaches the previous ones (``Galley.article`` is
        nullable), which would leave behind - on every single regeneration - a full set of
        `core.Galley` and `core.File` rows that nothing points at any more, and as many files in
        the article's folder.

        A galley that some other record still refers to (the typesetting assignment it was created
        in, or a proofing round it took part in) is only detached: there it is part of that
        record's own history, not of the article's current galleys.

        The rows are deleted inside the caller's transaction, so that a rollback brings them back;
        the files are unlinked only once that transaction has been committed, so that a rollback
        cannot leave the restored rows pointing at files that are gone.

        :param galleys: the galleys that are not the article's any more.
        :type galleys: list[Galley]
        """
        files_to_delete: list[JanewayFile] = []
        for galley in galleys:
            if self._is_still_in_use(galley):
                logger.debug(f"Galley {galley.pk} of {self.workflow.article.pk} is still in use: only detached.")
                continue
            files_to_delete.append(galley.file)
            files_to_delete.extend(
                # an image that another galley uses as well belongs to that one too
                image
                for image in galley.images.all()
                if not image.images.exclude(pk=galley.pk).exists()
            )
        if not files_to_delete:
            return

        paths = [Path(file.self_article_path()) for file in files_to_delete if file.article_id]
        # A queryset delete does not go through `core.File.delete()`, which would unlink the files
        # right away, before this transaction is known to succeed. The galleys go together with
        # their file: `Galley.file` cascades.
        JanewayFile.objects.filter(pk__in=[file.pk for file in files_to_delete]).delete()
        transaction.on_commit(lambda: self._unlink(paths))

    @staticmethod
    def _is_still_in_use(galley: Galley) -> bool:
        """
        Say whether some other record refers to the given galley.

        :param galley: the galley about to be deleted.
        :type galley: Galley

        :return: True if the galley belongs to a typesetting assignment or to a proofing round.
        :rtype: bool
        """
        return (
            TypesettingAssignment.objects.filter(galleys_created=galley).exists()
            or GalleyProofing.objects.filter(proofed_files=galley).exists()
        )

    @staticmethod
    def _unlink(paths: list[Path]):
        """
        Remove the given files from the filesystem.

        Their rows are already gone by now, so a file that cannot be removed is reported and left
        there: it is not worth failing a regeneration that has otherwise succeeded.

        :param paths: the files to remove.
        :type paths: list[Path]
        """
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exception:
                logger.error(f"Could not remove {path}: {exception}")

    def run(self) -> list[Galley]:
        """
        Store the uploaded archive and rebuild the article's galleys from it.

        Everything runs in a single transaction: if the generation fails, the article keeps the
        sources and the galleys that it had.

        :return: the galleys built from the uploaded archive.
        :rtype: list[Galley]

        :raises ValidationError: if the article's galleys cannot be rebuilt (see
            :meth:`check_conditions` and :meth:`generate_galleys`).
        """
        with transaction.atomic():
            # Lock the workflow: two clients replacing the sources at once would race over the
            # article's galleys.
            self.workflow = ArticleWorkflow.objects.select_for_update().get(pk=self.workflow.pk)
            green_light, reason = self.check_conditions()
            if not green_light:
                # Whatever check_conditions() refuses is about what the caller sent, so the API
                # reports it as a bad request rather than as the generation failure below.
                raise ValidationError(reason, code=INVALID_REQUEST_CODE)
            self.store_source_zip()
            galleys = self.generate_galleys()
            self.attach_galleys(galleys)
            # The article's page is cached, galleys included, so the site would go on serving the
            # generation we have just replaced. Drop the cache only once these galleys are
            # committed, or a concurrent request would just cache the previous ones again - this is
            # the same handler that a publication runs, for the same reason.
            transaction.on_commit(clear_cache)
        return galleys
