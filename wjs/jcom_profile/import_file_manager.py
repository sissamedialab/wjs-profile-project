"""Import article from wjapp."""

import io
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import Optional, Tuple

import requests
from core import files
from core.models import Galley, SupplementaryFile
from django.conf import settings
from django.core.files import File as DjangoFile
from django.core.files.base import ContentFile

import wjs.jcom_profile.import_logic as import_logic


@dataclass
class ImportFileManager:
    """Data class that manages the import of the files from the filesystem."""

    # This manager has as argument an instance of a subclass of BaseActionManager
    # for example AuthorSubmitRevisionAction(BaseActionManager)
    # corresponding to an action that must import the files of related version, for example
    # the author revision submission of the version 2 of the preprintid JCAP_001P_0123
    # Also the class manages the import of the edrep and repep files.
    action_manager: Optional["import_logic.BaseActionManager"] = None  # obsolete syntax
    file_types: Tuple[str, ...] = (  # Tuple -> tuple
        "tex",  # file preprintid.tex
        "pdf",  # file preprintid.pdf
        "source_tex",  # any source tex file not the "main"
        "source_zip",  # zip submitted by the author for each version
        "source_targz",  # targz submitted by the author for each version
        "figure",  # any figure file of the latex archive
        "edrep_tex",
        "edrep_pdf",
        "rerep_tex",
        "rerep_pdf",
        "attachment",  # wjapp attachments
        "author_annotation",  # file uploded by the author when sends the corrections
        "production_source",  # production tar.gz uploded by the typesetter
        "production_pdf",  # production pdf generated on wjapp
        "publication_pdf",  # publication pdf generated on wjapp
    )

    # Note: the archives contained in "preprintid/production/" should not be necessary
    # because reading the directory "submission/" we can import the correct
    # author source archive

    # TODO: necessary to manage publication version files (final files with the placeholder replaced)

    @classmethod
    def get_year_dir(cls, preprintid):
        """return part of the file path in the archive old"""
        return f"{preprintid[:4]}20{preprintid[-2:]}preprints"

    @classmethod
    def read_settings_archives_orig(cls, journal, preprintid):

        preprints_dir = "preprint"
        archive_path_current_orig = getattr(settings, f"WJAPP_{journal.code.upper()}_IMPORT_ARCHIVE_CURRENT", None)

        year_dir = ImportFileManager.get_year_dir(preprintid)
        archive_path_old_orig = getattr(settings, f"WJAPP_{journal.code.upper()}_IMPORT_ARCHIVE_OLD", None)

        return (f"{archive_path_current_orig}/{preprints_dir}", f"{archive_path_old_orig}/{year_dir}")

    @classmethod
    def read_settings_archives_dedup(cls, journal, preprintid):

        preprints_dir = "preprint"
        archive_path_current_dedup = getattr(
            settings, f"WJAPP_{journal.code.upper()}_IMPORT_ARCHIVE_CURRENT_DEDUP", None
        )

        year_dir = ImportFileManager.get_year_dir(preprintid)
        archive_path_old_dedup = getattr(settings, f"WJAPP_{journal.code.upper()}_IMPORT_ARCHIVE_OLD_DEDUP", None)

        return (f"{archive_path_current_dedup}/{preprints_dir}", f"{archive_path_old_dedup}/{year_dir}")

    @classmethod
    def dedup_archive_directory(cls, journal, preprintid):
        """run optimization of the directory content creating a tmp directory without duplicated files."""

        (archive_path_current_orig, archive_path_old_orig) = ImportFileManager.read_settings_archives_orig(
            journal, preprintid
        )

        (archive_path_current_dedup, archive_path_old_dedup) = ImportFileManager.read_settings_archives_dedup(
            journal, preprintid
        )

        preprintid_dir_current_dedup = f"{archive_path_current_dedup}/{preprintid}"
        preprintid_dir_current_orig = f"{archive_path_current_orig}/{preprintid}"
        if os.path.exists(preprintid_dir_current_orig) and not os.path.exists(preprintid_dir_current_dedup):
            os.makedirs(os.path.dirname(preprintid_dir_current_dedup), exist_ok=True)
            shutil.copytree(preprintid_dir_current_orig, preprintid_dir_current_dedup, dirs_exist_ok=True)
            ImportFileManager.run_dedup_script(journal, preprintid_dir_current_dedup)

        preprintid_dir_old_dedup = f"{archive_path_old_dedup}/{preprintid}"
        preprintid_dir_old_orig = f"{archive_path_old_orig}/{preprintid}"
        if os.path.exists(preprintid_dir_old_orig) and not os.path.exists(preprintid_dir_old_dedup):
            os.makedirs(os.path.dirname(preprintid_dir_old_dedup), exist_ok=True)
            shutil.copytree(preprintid_dir_old_orig, preprintid_dir_old_dedup, dirs_exist_ok=True)
            ImportFileManager.run_dedup_script(journal, preprintid_dir_old_dedup)

        return (archive_path_current_dedup, archive_path_old_dedup)

    @classmethod
    def run_dedup_script(cls, journal, tmp_path):
        """Run external script to deduplicate the preprintid directories"""

        script_path = getattr(settings, f"WJAPP_{journal.code.upper()}_IMPORT_DEDUP_SCRIPT", None)
        script_args = [tmp_path, "--recursive"]
        import_logic.logger.debug(f"Script {script_path} run on {tmp_path} ...")

        try:
            result = subprocess.run(
                [sys.executable, script_path] + script_args, capture_output=True, text=True, check=True
            )

            import_logic.logger.debug(f"Script { script_path} execute succefully")

            if result.stderr:
                import_logic.logger.error(f"{result.stderr}")

        except subprocess.CalledProcessError as e:
            import_logic.logger.error(f"Error during the execution: {e}")
            import_logic.logger.error(f"{e.stderr}")

    @classmethod
    def reset_preprintid_dedup(cls, journal, preprintid):
        """Delete preprintid current and old dedup folders"""

        (archive_path_current_dedup, archive_path_old_dedup) = ImportFileManager.read_settings_archives_dedup(
            journal, preprintid
        )

        preprintid_dir_current_dedup = Path(f"{archive_path_current_dedup}/{preprintid}")
        if preprintid_dir_current_dedup.exists():
            shutil.rmtree(preprintid_dir_current_dedup)

        preprintid_dir_old_dedup = Path(f"{archive_path_old_dedup}/{preprintid}")
        if preprintid_dir_old_dedup.exists():
            shutil.rmtree(preprintid_dir_old_dedup)

    def import_version_files(self, production_version=False):
        """Manages the import the files of the imported_version.

        The EDREP and REREP, AUANN, production_source are imported separately.
        """

        # remove previous source files relations already saved as historical files
        # necessary to clear() when the version import files starts because
        # there are two files preprintid.docx and submit.zip.
        # If the version is a production version the manual clear() must not be executed.
        if not production_version:
            self.action_manager.article.source_files.clear()
            self.action_manager.article.data_figure_files.clear()

        for data in self.get_list_of_files_to_import(
            production_version=production_version,
        ):
            djfile = self.read_file_from_archive(**data)
            import_logic.logger.debug(f"filetype: {data['filetype']} {djfile.name=}  {djfile.size=}")

            if data["filetype"] == "pdf":
                self.save_manuscript_pdf(djfile)
                continue
            if data["filetype"] == "tex":
                self.save_source_file(djfile, "TEX", "TEX manuscript file")
                continue
            if data["filetype"] == "source_zip":
                self.save_source_file(djfile, "ZIP", "ZIP source archive")
                continue
            if data["filetype"] == "source_targz":
                self.save_source_file(djfile, "TARGZ", "TARGZ source archive")
                continue
            if data["filetype"] == "production_pdf":
                self.save_pdf_galley(djfile)
                continue
            if data["filetype"] == "publication_pdf":
                self.save_pdf_galley(djfile, public=True)
                continue

            # attachments
            if data["filetype"] == "attachment" and production_version:
                ta_assignment = self.action_manager.article.articleworkflow.get_latest_typesetting_assignment(
                    only_completed=False,
                )
                dff_file = files.save_file_to_article(
                    djfile,
                    self.action_manager.article,
                    ta_assignment.typesetter,
                )
                dff_file.label = data["attachType"]
                dff_file.description = f"{data['attachTitle']} {data['attachDescription']}"
                dff_file.save()
                # for a production version, each attachment is a SF
                # TODO: verify the case of published version
                if not self.publicationid:
                    self.action_manager.article.supplementary_files.add(
                        SupplementaryFile.objects.create(file=dff_file)
                    )
                continue

            # for a review version the attachment is a data figure file
            if data["filetype"] == "attachment" and not production_version:
                self.save_data_figure_file(djfile, data)

    def get_list_of_files_to_import(self, production_version=False):
        """Read archive and for the current preprintid an version extract the list of file and file type to import"""

        # For the production version not publication version we save
        # PREPRINT.pdf as TA.galleys_created public=False
        #
        # For the publication version:
        #
        # ATM we import for the published version only the PUBLICATIONID.pdf
        # as TA.galleys_created public=True
        #
        # TODO: final supplementary supplementary have to be imported
        # TODO: publication source has to be imported (with placeholders replaced)
        if production_version:
            publication_version = self.action_manager.imported_version_state_cod == 22
            import_logic.logger.debug(f"{publication_version=}")
            if publication_version:
                filename = f"{self.action_manager.publicationid}.pdf"
                filetype = "publication_pdf"
            else:
                filename = f"{self.action_manager.preprintid}.pdf"
                filetype = "production_pdf"

            return [
                {
                    "archive_path": None,
                    "subdir": "",
                    "filename": filename,
                    "filetype": filetype,
                    "description": None,
                    "title": None,
                }
            ]

        # PREPRINT.pdf. PREPRINTID.tex is not imported because we want to have in wjs only the author source
        # and the PREPRINTID.pdf generated on wjapp
        files_list = [
            {
                "archive_path": None,
                "subdir": "",
                "filename": f"{self.action_manager.preprintid}.pdf",
                "filetype": "pdf",
                "description": None,
                "title": None,
            },
        ]

        # source submission archive (tar.gz or .zip)
        submission_archive_list = []

        path_sub_arch_current = (
            Path(self.action_manager.current_archive)
            / f"{self.action_manager.preprintid}"
            / f"{self.action_manager.imported_version_num} / 'submission'"
        )

        archive_path_found = None
        archive_dir = "submission"
        # search in current
        for item in chain(path_sub_arch_current.rglob("*.tar.gz"), path_sub_arch_current.rglob("*.zip")):
            if item.is_file():
                submission_archive_list.append(item)
                archive_path_found = self.action_manager.current_archive
                import_logic.logger.debug(f"submission archive in current: {item=}")

        if not submission_archive_list:
            if not Path(f"{self.action_manager.old_archive}/{self.action_manager.preprintid}/").exists():
                # build in current
                submission_archive_list.append(self.build_submission_zip_from_main_directory(archive="current"))
                archive_path_found = self.action_manager.current_archive
                archive_dir = "build_archive_dir"
            else:
                path_sub_arch_old = Path(
                    f"{self.action_manager.old_archive}/{self.action_manager.preprintid}/"
                    f"{self.action_manager.imported_version_num}/submission"
                ).resolve()
                for item in chain(path_sub_arch_old.rglob("*.tar.gz"), path_sub_arch_old.rglob("*.zip")):
                    if item.is_file():
                        submission_archive_list.append(item)
                        archive_path_found = self.action_manager.old_archive
                        import_logic.logger.debug(f"submission archive in old: {item=}")

                if not submission_archive_list:
                    # build in old
                    submission_archive_list.append(self.build_submission_zip_from_main_directory(archive="old"))
                    archive_path_found = self.action_manager.old_archive
                    archive_dir = "build_archive_dir"

        # build item to add the submission archive to the files list, warning if not found or found more than one
        if not submission_archive_list:
            import_logic.logger.error(f"Not found submission archive {submission_archive_list}")
        elif len(submission_archive_list) > 1:
            import_logic.logger.error(
                f"Found {len(submission_archive_list)} submission archives {submission_archive_list}"
            )
        else:
            exts = submission_archive_list[0].suffixes
            ext = "".join(exts)
            if ext in [".tar.gz"]:
                filetype = "source_targz"
            if ext in [".zip"]:
                filetype = "source_zip"

            submission_archive = {
                "archive_path": archive_path_found,
                "subdir": f"{archive_dir}",
                "filename": f"{submission_archive_list[0].name}",
                "filetype": filetype,
                "description": None,
                "title": None,
            }
            files_list.append(submission_archive)

        # read attachments data from wjapp database and add the file files to the list
        # with the same format, pdf, zip, ...
        # the original name of the attachment file is not imported but it is not relevant

        wjapp_attachments = self.action_manager.read_attachments_data()
        for dff_data in wjapp_attachments:
            files_list.append(
                {
                    "archive_path": None,
                    "subdir": "attachments",
                    "filename": f"{dff_data['attachID']}.{dff_data['attachFormat']}",
                    "filetype": "attachment",
                    "description": dff_data["attachDescription"],
                    "title": dff_data["attachTitle"],
                },
            )

        import_logic.logger.debug(
            f"File List Found for version {self.action_manager.imported_version_num=}  {files_list=}"
        )
        return files_list

    def build_submission_zip_from_main_directory(self, archive=None):
        """create submission zip in build_archive_dir from main dir and submission dir.

        The deduplication lets in submission dir files which are different from the preprintid dir files."""

        # directory definition
        if archive == "current":
            version_dir = Path(
                f"{self.action_manager.current_archive}/{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}"
            )
        else:
            version_dir = Path(
                f"{self.action_manager.old_archive}/{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}"
            )

        import_logic.logger.debug(f"PATH version dir:   {version_dir=}")

        subdir = version_dir / "build_archive_dir"
        zip_name = f"{self.action_manager.preprintid}_v{self.action_manager.imported_version_num}_submit.zip"
        zip_path = subdir / zip_name

        # exclusions
        exclude_items = {
            "work",
            "EDREP",
            "REREP",
            "AUANN",
            "production",
            "publication",
            "pitstop",
            "build_archive_dir",
            f"{zip_name}",
        }

        # check existance subdir
        subdir.mkdir(parents=True, exist_ok=False)

        import_logic.logger.debug(f"{zip_path=}")
        # create zip archive
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # iterate on all files and directory in preprint dir
            for item in version_dir.rglob("*"):
                # calculate relative path
                rel_path = item.relative_to(version_dir)
                import_logic.logger.debug(f"{rel_path=}")

                # verify if the item or parent is to exclude
                import_logic.logger.debug(f"EXCLUDING: {rel_path.parts=}")
                if any(part in exclude_items for part in rel_path.parts):
                    import_logic.logger.debug(f"EXCLUDED:  {rel_path=}")
                    continue

                # add file/directory to zip
                if item.is_file():
                    zipf.write(item, arcname=rel_path)
                    import_logic.logger.debug(f"ADDED:  {rel_path=}")
                elif item.is_dir() and any(item.iterdir()):
                    # do not add empty directories
                    import_logic.logger.debug(f"ADDED:  {rel_path=}")
                    zipf.write(item, arcname=str(rel_path) + "/")
        return zip_path

    def build_production_source_targz_from_main_directory(self, archive=None):
        """Create production targz source in build_archive_dir from
           - base preprint dir
           - submission/
           - publication/currentHidden/

        Adding currentHidden to the archive we are sure to import also the typesetter source
        file with the placeholders replaced.
        The deduplication lets in submission dir files which are different from the preprintid dir files.
        """

        import tarfile

        import_logic.logger.debug(f"PATH:   {self.action_manager.old_archive=}")

        # directory definition
        if archive == "current":
            version_dir = Path(
                f"{self.action_manager.current_archive}/{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}"
            )
        else:
            version_dir = Path(
                f"{self.action_manager.old_archive}/{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}"
            )

        import_logic.logger.debug(f"PATH version dir:   {version_dir=}")

        subdir = version_dir / "build_archive_dir"
        targz_name = f"{self.action_manager.preprintid}.tar.gz"
        targz_path = subdir / targz_name

        # exclusions
        exclude_items = {
            "work",
            "EDREP",
            "REREP",
            "AUANN",
            "production",
            "pitstop",
            "build_archive_dir",
        }

        # check existance subdir
        subdir.mkdir(parents=True, exist_ok=False)

        import_logic.logger.debug(f"{targz_path=}")

        # create tar.gz archive
        with tarfile.open(targz_path, "w:gz") as tar:
            # iterate on all files and directory in preprint dir
            for item in version_dir.rglob("*"):
                # calculate relative path
                rel_path = item.relative_to(version_dir)
                import_logic.logger.debug(f"{rel_path=}")

                # verify if the item or parent is to exclude
                import_logic.logger.debug(f"ANALISING: {rel_path.parts=}")
                import_logic.logger.debug(f"Match: {any(part in exclude_items for part in rel_path.parts)}")
                if any(part in exclude_items for part in rel_path.parts):
                    import_logic.logger.debug(f"EXCLUDED:  {rel_path=}")
                    continue

                # exclude empty dir
                if item.is_dir() and not any(item.iterdir()):
                    import_logic.logger.debug(f"EXCLUDED EMPTY:  {rel_path=}")
                    continue

                # add file/directory to tar
                tar.add(item, arcname=rel_path, recursive=False)
                import_logic.logger.debug(f"ADDED:  {rel_path=}")

        return targz_path

    def read_file_from_archive(
        self, archive_path: str, subdir: str, filename: str, filetype: str, description: str, title: str
    ) -> DjangoFile:
        """
        Read a file stored inside an archive directory on the filesystem and return it as a DjangoFile.
        The archive here is a directory on disk (not necessarily a zip). This function does NOT open
        nested zip files — it only reads the raw bytes of the target file and wraps them in a DjangoFile
        ready to be saved via a Django model FileField or storage.
        If the file is not in the current archive, try on the old archive.
        Parameters
        - archive path - optional, if not defined, try in order current and old
        - archive_path_current: full path to the archive directory on disk.
        - subdir: the subdirectory, e.g. 'submission' dir
        - filename: the name of the file
        - filetype: one of the allowed file types; validated against the predefined list.

        Returns
        - DjangoFile wrapping a ContentFile containing the file bytes, with name set to filename.

        Raises
        - FileNotFoundError: if archive directory or target file does not exist.
        - ValueError: if filetype is not allowed.
        """

        # Validate filetype
        if filetype not in self.file_types:
            raise ValueError(f"Unknown filetype: {filetype!r}. Allowed types: {', '.join(self.file_types)}")

        if not archive_path:
            # search in the current archive directory
            candidate = Path(
                f"{self.action_manager.current_archive}/{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/{subdir}/{filename}"
            ).resolve()
            import_logic.logger.debug(f"search candidate {filetype} in archive current: {candidate=}")

            if not candidate.exists() or not candidate.is_file():
                # fallback search in the old archive directory
                candidate = Path(
                    f"{self.action_manager.old_archive}/{self.action_manager.preprintid}/"
                    f"{self.action_manager.imported_version_num}/{subdir}/{filename}"
                ).resolve()
                import_logic.logger.debug(f"fallback search candidate {filetype} in the archive old: {candidate}")
                if not candidate.exists() or not candidate.is_file():
                    raise FileNotFoundError(f"File {filetype} not found: {filename}")
        else:
            # provided archive path
            candidate = Path(
                f"{archive_path}/{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/{subdir}/{filename}"
            ).resolve()
            import_logic.logger.debug(f"search candidate {filetype} in provided archive path: {candidate=}")
            if not candidate.exists() or not candidate.is_file():
                raise FileNotFoundError(f"File {filetype} not found: {filename} in provided {archive_path}")

        # Read bytes
        with candidate.open("rb") as f:
            content = f.read()

        # Return DjangoFile wrapping ContentFile
        return DjangoFile(ContentFile(content), name=filename)

    def import_edrep_file_source(self, edrep):
        """Import the edrep source tex of the imported_version during the review action.

        return: the content of the edrep tex source
        """

        # edrep file: to be managed by the editor decision action
        path_edrep_current = (
            Path(self.action_manager.current_archive)
            / f"{self.action_manager.preprintid}"
            / f"{self.action_manager.imported_version_num}"
            / "EDREP"
            / f"{edrep}"
            / f"{edrep}.tex"
        )
        import_logic.logger.debug(f"path edrep current: {path_edrep_current}")
        if Path(path_edrep_current).exists():
            with open(path_edrep_current, encoding="utf-8") as f:
                import_logic.logger.debug(f"found current {edrep}.tex")
                return f.read()

        path_edrep_old = (
            Path(self.action_manager.old_archive)
            / f"{self.action_manager.preprintid}"
            / f"{self.action_manager.imported_version_num}"
            / "EDREP"
            / f"{edrep}"
            / f"{edrep}.tex"
        )
        import_logic.logger.debug(f"path edrep old: {path_edrep_old}")
        if Path(path_edrep_old).exists():
            with open(path_edrep_old, encoding="utf-8") as f:
                import_logic.logger.debug(f"found old {edrep}.tex")
                return f.read()

    def import_rerep_file_pdf(self, rerep):
        """Import the rerep files of the specific referee of the imported_version.

        The rerep file is imported during the related review actions, not
        when the version paper files are imported.
        """

        # rerep file: to be managed by the send report action
        path_rerep_current = (
            Path(self.action_manager.current_archive)
            / f"{self.action_manager.preprintid}"
            / f"{self.action_manager.imported_version_num}"
            / "REREP"
            / f"{rerep}"
        )
        if path_rerep_current.exists() and path_rerep_current.rglob(f"{rerep}.pdf"):
            data = {
                "archive_path": self.action_manager.current_archive,
                "subdir": f"REREP/{rerep}",
                "filename": f"{rerep}.pdf",
                "filetype": "rerep_pdf",
                "description": None,
                "title": None,
            }
            return self.read_file_from_archive(**data)

        path_rerep_old = (
            Path(self.action_manager.old_archive)
            / f"{self.action_manager.preprintid}"
            / f"{self.action_manager.imported_version_num}"
            / "REREP"
            / f"{rerep}"
        )
        if path_rerep_old.exists() and path_rerep_old.rglob(f"{rerep}.pdf"):
            data = {
                "archive_path": self.action_manager.old_archive,
                "subdir": f"REREP/{rerep}",
                "filename": f"{rerep}.pdf",
                "filetype": "rerep_pdf",
                "description": None,
                "title": None,
            }
            return self.read_file_from_archive(**data)

    def get_production_source(self):
        """Get the production source archive which is managed by the typesetter upload action."""

        archive_dir = "submission/"
        current_production_source_path = Path(
            f"{self.action_manager.current_archive}/"
            f"{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/"
            f"{archive_dir}"
            f"{self.action_manager.preprintid}.tar.gz"
        )
        if current_production_source_path.exists():
            archive_path_found = self.action_manager.current_archive
        else:
            if not Path(f"{self.action_manager.old_archive}/" f"{self.action_manager.preprintid}").exists():
                archive_path_found = self.action_manager.current_archive
                self.build_production_source_targz_from_main_directory(archive="current")
                archive_dir = "build_archive_dir"
            else:
                archive_path_found = self.action_manager.old_archive
                old_production_source_path = Path(
                    f"{self.action_manager.old_archive}/"
                    f"{self.action_manager.preprintid}/"
                    f"{self.action_manager.imported_version_num}/"
                    f"{archive_dir}"
                    f"{self.action_manager.preprintid}.tar.gz"
                )
                if not old_production_source_path.exists():
                    self.build_production_source_targz_from_main_directory(archive="old")
                    archive_dir = "build_archive_dir"

        data = {
            "archive_path": archive_path_found,
            "subdir": f"{archive_dir}",
            "filename": f"{self.action_manager.preprintid}.tar.gz",
            "filetype": "production_source",
            "description": None,
            "title": None,
        }
        djfile = self.read_file_from_archive(**data)
        import_logic.logger.debug(f"filetype: {data['filetype']} {djfile.name=}  {djfile.size=}")
        return djfile

    def get_author_annotation_file(self, author_annotation):
        """Get the author annotation file.

        return: (mime type, file)"""

        # author annotation: to be managed by the author send corrections action
        path_auann_current = (
            Path(self.action_manager.current_archive)
            / f"{self.action_manager.preprintid}"
            / f"{self.action_manager.imported_version_num}"
            / "AUANN"
            / f"{author_annotation}"
        )

        # search in current
        found = path_auann_current.rglob(f"{author_annotation}.*")
        data = {}
        if found:
            for item in found:
                if item.is_file():
                    import_logic.logger.debug(f"auann in current: {item=}")
                    data = {
                        "archive_path": self.action_manager.current_archive,
                        "subdir": f"AUANN/{author_annotation}",
                        "filename": f"{item.name}",
                        "filetype": "author_annotation",
                        "description": None,
                        "title": None,
                    }
        else:
            path_auann_old = (
                Path(self.action_manager.old_archive)
                / f"{self.action_manager.preprintid}"
                / f"{self.action_manager.imported_version_num}"
                / "AUANN"
                / f"{author_annotation}"
            ).resolve()
            for item_old in path_auann_old.rglob(f"{author_annotation}.*"):
                if item_old.is_file():
                    import_logic.logger.debug(f"auann in old: {item_old=}")
                    data = {
                        "archive_path": f"{self.action_manager.old_archive}",
                        "subdir": f"AUANN/{author_annotation}",
                        "filename": f"{item_old.name}",
                        "filetype": "author_annotation",
                        "description": None,
                        "title": None,
                    }

        if not data:
            import_logic.logger.warning(f"author annotation not found {author_annotation}")
            return (None, None)

        djfile = self.read_file_from_archive(**data)
        import_logic.logger.debug(f"filetype: {data['filetype']} {djfile.name=}  {djfile.size=}")

        # TODO: extension management used also for contentype
        parts = djfile.name.split(".")
        # FIX: extension is used as mime type application/extension
        # check action AU_SENDS_CORRECT
        extension = ".".join(parts[-2:]) if len(parts) > 2 else parts[-1]
        if extension == "tar.gz":
            extension = "gzip"
        import_logic.logger.warning(f"extension to be verified also for mime type: {extension}")
        return (extension, djfile)

    def save_source_file(self, source_dj, label, desc):
        """Save tex, zip/tar.gz as source archive"""

        # there is more than one source file for version, therefore
        # they are not clear()

        source_file = files.save_file_to_article(
            source_dj,
            self.action_manager.article,
            self.action_manager.article.correspondence_author,
        )
        self.action_manager.article.source_files.add(source_file)
        source_file.label = label
        source_file.description = desc
        source_file.save()
        self.action_manager.article.save()
        import_logic.logger.debug(f"saved {desc}: {source_dj}")
        return

    def save_manuscript_pdf(self, manuscript_dj):
        """Save PDF manuscript"""

        # remove previous files relations already saved as historical files
        # the manuscript has only the current files
        self.action_manager.article.manuscript_files.clear()

        manuscript_file = files.save_file_to_article(
            manuscript_dj,
            self.action_manager.article,
            self.action_manager.article.correspondence_author,
        )
        self.action_manager.article.manuscript_files.add(manuscript_file)
        manuscript_file.label = "PDF"
        manuscript_file.description = "PDF manuscript"
        manuscript_file.save()
        self.action_manager.article.save()
        import_logic.logger.debug(f"saved pdf file {manuscript_dj}")
        return

    def save_pdf_galley(self, pdf_galley_dj, public=False):
        """Save PDF production version in TA.galleys_created"""

        assignment = self.action_manager.article.articleworkflow.get_latest_typesetting_assignment(
            only_completed=False
        )

        # necessary to avoid errors until action "back to typesetter"
        # is implemented, if action TYP_UPLOADS_FOR_PM happens twice like
        # in JCOM_017A_0624
        assignment.galleys_created.clear()

        assert not assignment.galleys_created.exists(), (
            f"We have {assignment.galleys_created.count()} galleys on the TA "
            f"for round {assignment.round.round_number}. Expected none!"
        )

        pdf_galley_file = files.save_file_to_article(
            pdf_galley_dj,
            self.action_manager.article,
            assignment.typesetter,
        )
        pdf_galley = Galley.objects.create(
            file=pdf_galley_file, label="PDF", type="pdf", article=self.action_manager.article, public=public
        )
        assignment.galleys_created.add(pdf_galley)

        assignment.round.article.articleworkflow.save()
        import_logic.logger.debug(f"saved production/publication pdf file {pdf_galley_dj}")
        return

    def save_data_figure_file(self, dff_dj, data):
        """Save wjapp attachment as data figure file"""

        dff_file = files.save_file_to_article(
            dff_dj,
            self.action_manager.article,
            self.action_manager.article.correspondence_author,
        )
        self.action_manager.article.data_figure_files.add(dff_file)
        dff_file.label = data["filetype"]
        dff_file.description = f"{data['title']} {data['description']}"
        dff_file.save()
        self.action_manager.article.save()
        import_logic.logger.debug(f"saved attachment {dff_dj}")
        return


@dataclass
class ImportFileFakeManager:
    """Data class that manages the import of the files as fake files."""

    # LEGACY. Needs validation before to use.

    # This manager has as argument an instance of a subclass of BaseActionManager
    # for example AuthorSubmitRevisionAction(BaseActionManager)
    # corresponding to an action that must import the files of related version, for example
    # the author revision submission of the version 2 of the preprintid JCAP_001P_0123
    # Also the class manages the import of the edrep and rerep files.
    action_manager: Optional["import_logic.BaseActionManager"] = None  # obsolete syntax

    def import_files_fake(self, production_version=False):
        """Save fake files for imported version."""

        # TBV: action admin resets editor will be skip in the import. New version is created when
        #     the editor is assigned. Verify that the import of the files is coerent in wjapp
        # TODO: import files is different for imported_version_state_cod published
        #       pubid.pdf instead of preprintid.pdf (fake files)
        #
        # fake pdf published version pubid.pdf (no source): "PDF", "pdf",
        #   JCOM_003N_0623/8/JCOM_2305_2024_N03.pdf&fileType=pdf
        #
        # published version: create fake tar.gz loaded by typesetter from previous version
        #       JCOM_003N_0623/7/submission/JCOM_003N_0623.tar.gz
        # TODO: also download and add (to be decided how) to the tar.gz from current_hidden
        #       JCOM_003N_0623.tex which has the placeholders replaced
        #
        # fake preprintid.docx, preprintid.pdf for not published version: "ZIP", "zip"
        #   JCOM_003N_0623/7/JCOM_003N_0623.docx&fileType=docx
        #   JCOM_003N_0623/7/JCOM_003N_0623.pdf&fileType=pdf
        #
        #   TBV if necessary:
        #   Note: other fake files like Figure1.docx submitted by the author
        #         created also a fake source file produduction/JCOM_003N_0623.zip which
        #         contains submission/
        #
        # attachments read data from db for each attachment (no source file name)
        #   JCOM_003N_0623/1/attachments/JCOM_011A_0623_ATTACH00060623.pdf&fileType=Table
        # and create fake files

        # wjapp version state 22 is the published version
        if self.action_manager.imported_version_state_cod == 22:
            # TBV:
            # we don't want to import files for the published version because the final
            # galleys and supplementary are already imported when the published paper
            # has been imported
            return

        elif production_version:
            import_logic.logger.debug(f"production version: {production_version}")
            fake_pdf_file = self.action_manager.create_minimal_djangofile(
                f"{self.action_manager.preprintid}.pdf", "pdf"
            )
            ImportFileWebManager(self.action_manager).save_pdf_galley(fake_pdf_file)

        else:
            fake_pdf_file = self.action_manager.create_minimal_djangofile(
                f"{self.action_manager.preprintid}.pdf", "pdf"
            )
            ImportFileWebManager(self.action_manager).save_manuscript(fake_pdf_file)

            # remove previous source files relations already saved as historical files
            # necessary to clear() when the version import files starts because
            # there are two files preprintid.docx and submit.zip
            self.action_manager.article.source_files.clear()

            fake_tex_file = self.action_manager.create_minimal_djangofile(
                f"{self.action_manager.preprintid}.tex", "tex"
            )
            ImportFileWebManager(self.action_manager).save_source(fake_tex_file, "tex")

            # we want to create the fake source files sent by the author,
            # e.g. JCOM_008A_0125: Figure1.docx, Figure2.docx ...
            fake_zip_file = self.action_manager.create_minimal_djangofile(
                f"{self.action_manager.preprintid}.zip", "zip"
            )
            ImportFileWebManager(self.action_manager).save_source(fake_zip_file, "zip")

        # read attachments data from wjapp and save each esm with the same format, pdf, zip, ...
        # the original name of the attachment file is not imported but it is not relevant
        if not production_version:
            self.action_manager.article.data_figure_files.clear()
        wjapp_attachments = self.action_manager.read_attachments_data()
        wjapp_fake_prod_attachments = []
        for dff_data in wjapp_attachments:
            dff_dj = self.action_manager.create_minimal_djangofile(
                f"{dff_data['attachID']}.{dff_data['attachFormat']}", f"{dff_data['attachFormat']}"
            )
            if production_version:
                ta_assignment = self.action_manager.article.articleworkflow.get_latest_typesetting_assignment(
                    only_completed=False,
                )
                dff_file = files.save_file_to_article(
                    dff_dj,
                    self.action_manager.article,
                    ta_assignment.typesetter,
                )
                dff_file.label = dff_data["attachType"]
                dff_file.description = f"{dff_data['attachTitle']} {dff_data['attachDescription']}"
                dff_file.save()
                # for a production version, each attachment is as SF in a list
                wjapp_fake_prod_attachments.append(SupplementaryFile.objects.create(file=dff_file))
            else:
                # in review version each attachments is saved as DFF
                ImportFileWebManager(self.action_manager).save_data_figure_file(dff_dj, dff_data)

        # if the production version is not of a published paper
        # the prepared list is saved as SF list
        if production_version and not self.action_manager.publicationid and wjapp_fake_prod_attachments:
            self.action_manager.article.supplementary_files.set(wjapp_fake_prod_attachments)

    def create_minimal_djangofile(self, filename: str, filetype: str) -> DjangoFile:
        """
        Create and return a minimal DjangoFile for the specified type.
        Supported filetype values: 'pdf', 'tex', 'zip', 'tar.gz', 'tar', 'targz', 'jpg', 'jpeg', 'png'
        """
        ft = filetype.lower()
        if ft == "pdf":
            content = (
                b"%PDF-1.1\n%\xE2\xE3\xCF\xD3\n1 0 obj\n"
                b"<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                b"xref\n0 1\n0000000000 65535 f \n"
                b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
            )
            return DjangoFile(ContentFile(content), name=filename)

        if ft == "tex":
            tex_text = r"""\documentclass{article}
\begin{document}
Minimal TeX file.
\end{document}
"""
            return DjangoFile(ContentFile(tex_text.encode("utf-8")), name=filename)

        if ft in ("zip",):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("readme.txt", "Minimal ZIP content\n")
            buf.seek(0)
            return DjangoFile(ContentFile(buf.read()), name=filename)

        if ft in ("tar.gz", "targz", "tar"):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                info = tarfile.TarInfo("readme.txt")
                data = b"Minimal tar.gz content\n"
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            buf.seek(0)
            return DjangoFile(ContentFile(buf.read()), name=filename)

        if ft in ("jpg", "jpeg"):
            # Minimal valid JPEG (JFIF) header with no image data — decoders accept it as a small image.
            jpeg = (
                b"\xFF\xD8"  # SOI
                b"\xFF\xE0\x00\x10"  # APP0 marker, length 16
                b"JFIF\x00"  # Identifier
                b"\x01\x01\x00\x00\x01\x00\x01\x00"  # Version, density, thumbnail
                b"\xFF\xD9"  # EOI
            )
            return DjangoFile(ContentFile(jpeg), name=filename)

        if ft == "png":
            # Minimal valid PNG: signature + IHDR chunk (1x1, truecolor, no compression/filter/interlace)
            # + IDAT empty + IEND
            png = (
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\x0dIHDR"
                b"\x00\x00\x00\x01"  # width:1
                b"\x00\x00\x00\x01"  # height:1
                b"\x08"  # bit depth
                b"\x02"  # color type: truecolor
                b"\x00\x00\x00"  # compression, filter, interlace
                b"\x90wS\xde"  # CRC (precomputed for this IHDR)
                b"\x00\x00\x00\x0aIDAT"
                b"\x08\xd7c\xf8\x0f\x00\x01\x01\x01\x00"  # small deflate data (may be accepted)
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            return DjangoFile(ContentFile(png), name=filename)

        # fallback: empty binary
        return DjangoFile(ContentFile(b""), name=filename)

    def file_fake_source_prod(self):
        "save minimal fake targz file loaded by typesetter."

        return self.action_manager.create_minimal_djangofile(f"{self.action_manager.preprintid}.tar.gz", "tar.gz")


@dataclass
class ImportFileWebManager:
    """Data class that manages the import of the files from web site."""

    # LEGACY. Needs validation before to use.

    # This manager has as argument an instance of a subclass of BaseActionManager
    # for example AuthorSubmitRevisionAction(BaseActionManager)
    # corresponding to an action that must import the files of related version, for example
    # the author revision submission of the version 2 of the preprintid JCAP_001P_0123
    # Also the class manages the import of the edrep and rerep files.
    action_manager: Optional["import_logic.BaseActionManager"] = None  # obsolete syntax

    def import_files_from_web(self, production_version=False):
        """Downloads and save files for imported version."""

        # TBV: action admin resets editor will be skip in the import. New version is created when
        #     the editor is assigned. Verify that the import of the files is coerent in wjapp
        # TODO: import files is different for imported_version_state_cod published
        #       pubid.pdf instead of preprintid.pdf
        #
        # pdf published version pubid.pdf (no source): "PDF", "pdf",
        #   JCOM_003N_0623/8/JCOM_2305_2024_N03.pdf&fileType=pdf
        #
        # published version: download tar.gz loaded by typesetter from previous version
        #       JCOM_003N_0623/7/submission/JCOM_003N_0623.tar.gz
        # TODO: also download and add (to be decided how) to the tar.gz from current_hidden
        #       JCOM_003N_0623.tex which has the placeholders replaced
        #
        # download preprintid.docx, preprintid.pdf for not published version: "ZIP", "zip"
        #   JCOM_003N_0623/7/JCOM_003N_0623.docx&fileType=docx
        #   JCOM_003N_0623/7/JCOM_003N_0623.pdf&fileType=pdf
        #
        #   Note: to not loose other files like Figure1.docx submitted by the author
        #         imported also as source file produduction/JCOM_003N_0623.zip which
        #         contains submission/
        #
        # attachments read data from db for each attachment (no source file name)
        #   JCOM_003N_0623/1/attachments/JCOM_011A_0623_ATTACH00060623.pdf&fileType=Table

        # wjapp version state 22 is the published version
        if self.action_manager.imported_version_state_cod == 22:
            # we don't want to import files for the published version because the final
            # galleys and supplementary are already imported when the published paper
            # has been imported
            return

        elif production_version:
            import_logic.logger.debug(f"production version: {production_version}")
            response_pdf_galley_prod = self.action_manager.download_pdf_galley_prod()

            if response_pdf_galley_prod.headers["Content-Length"] != "0":
                pdf_galley_prod_dj = import_logic.file_from_response(
                    response_pdf_galley_prod, f"{self.action_manager.preprintid}.pdf"
                )
                self.action_manager.save_pdf_galley(pdf_galley_prod_dj)

        else:
            known_missing_manuscript = {("JCOM_001E_0422", 1), ("JCOM_002A_0717", 1), ("JCOM_018A_0923", 1)}
            if (self.action_manager.preprintid, self.action_manager.imported_version_num) in known_missing_manuscript:
                import_logic.logger.debug(
                    f"{self.action_manager.preprintid} / {self.action_manager.imported_version_num} "
                    f"(missing manuscript - known case)"
                )
            else:
                response_manuscript = self.action_manager.download_manuscript()
                if response_manuscript.headers["Content-Length"] != "0":
                    manuscript_dj = import_logic.file_from_response(
                        response_manuscript, f"{self.action_manager.preprintid}.pdf"
                    )
                    self.action_manager.save_manuscript(manuscript_dj)

            # remove previous source files relations already saved as historical files
            # necessary to clear() when the version import files starts because
            # there are two files preprintid.docx and submit.zip
            self.action_manager.article.source_files.clear()

            (response_source, doc_type) = self.action_manager.download_source()
            if response_source.headers["Content-Length"] != "0":
                source_dj = import_logic.file_from_response(
                    response_source, f"{self.action_manager.preprintid}.{doc_type}"
                )
                self.action_manager.save_source(source_dj, doc_type)

            # we want to download all the source files sent by the author,
            # e.g. JCOM_008A_0125: Figure1.docx, Figure2.docx ...
            (response_source_compressed, doc_type_compressed) = (
                self.action_manager.download_source_compressed_archive()
            )
            if response_source_compressed.headers["Content-Length"] != "0":
                # returns a zip "submit.zip" containing only the  subdirectory "submission/" of the production zip
                # with the originals files sent by the author
                submit_source_compressed_dj = self.action_manager.extract_subdirectory_to_zip(
                    response_source_compressed,
                    f"{self.action_manager.preprintid}/submission/",
                    f"submit.{doc_type_compressed}",
                )
                self.action_manager.save_source(submit_source_compressed_dj, doc_type_compressed)

        # read attachments data from wjapp and save each esm with the same format, pdf, zip, ...
        # the original name of the attachment file is not imported but it is not relevant

        if not production_version:
            self.action_manager.article.data_figure_files.clear()
        wjapp_attachments = self.action_manager.read_attachments_data()
        wjapp_prod_attachments = []
        for dff_data in wjapp_attachments:
            dff_response = self.action_manager.download_wjapp_attachment(dff_data)
            if dff_response.headers["Content-Length"] != "0":
                dff_dj = import_logic.file_from_response(
                    dff_response, f"{dff_data['attachID']}.{dff_data['attachFormat']}"
                )
                if production_version:
                    ta_assignment = self.action_manager.article.articleworkflow.get_latest_typesetting_assignment(
                        only_completed=False,
                    )
                    dff_file = files.save_file_to_article(
                        dff_dj,
                        self.action_manager.article,
                        ta_assignment.typesetter,
                    )
                    dff_file.label = dff_data["attachType"]
                    dff_file.description = f"{dff_data['attachTitle']} {dff_data['attachDescription']}"
                    dff_file.save()
                    # for a production version, each attachment is as SF in a list
                    wjapp_prod_attachments.append(SupplementaryFile.objects.create(file=dff_file))
                else:
                    # in review version each attachments is saved as DFF
                    self.action_manager.save_data_figure_file(dff_dj, dff_data)

        # if the production version is not of a published paper
        # the prepared list is saved as SF list
        if production_version and not self.action_manager.publicationid and wjapp_prod_attachments:
            self.action_manager.article.supplementary_files.set(wjapp_prod_attachments)

    def extract_subdirectory_to_zip(self, response, subdirectory, name):
        """return a DF zip containing only the subdirectory"""

        memory_zip = BytesIO()

        with zipfile.ZipFile(BytesIO(response.content), "r") as zip_ref:
            # Create a new ZIP file in memory
            with zipfile.ZipFile(memory_zip, "w") as new_zip:
                # List all files in the zip file
                for file_info in zip_ref.infolist():
                    # Check if the file is in the specified subdirectory i.e. JCOM_002N_1122/submission/
                    import_logic.logger.debug(f"file name: {file_info} {subdirectory}")
                    if file_info.filename.startswith(subdirectory):
                        try:
                            # Read the file into memory
                            with zip_ref.open(file_info.filename) as file:
                                # Read the content safely
                                content = file.read()

                                # Write the file to the new ZIP archive in memory
                                new_zip.writestr(file_info.filename, content)

                        except Exception as e:
                            import_logic.logger.error(f"Error processing file {file_info.filename}: {e}")
                            raise ValueError("Error processing file")

        return DjangoFile(memory_zip, name)

    # manuscript
    def download_manuscript(self):
        """Download pdf manuscript for imported version."""

        file_url = (
            f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/"
            f"{self.action_manager.preprintid}.pdf&fileType=pdf"
        )
        import_logic.logger.debug(f"{file_url=}")
        response = self.action_manager.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            import_logic.logger.error(
                f"check wjapp login credentials empty file pdf downloaded: {response.headers['Content-Length']}"
            )
            raise ValueError("Empty pdf downloaded")
        return response

    # production version files

    def download_pdf_galley_prod(self):
        """Download pdf galley for production imported version."""

        file_url = (
            f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/{self.action_manager.preprintid}.pdf&fileType=pdf"
        )

        if self.action_manager.preprintid == "JCOM_003A_0615" and self.action_manager.imported_version_num == 5:
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}"
                f"/publication/currentHidden/{self.action_manager.preprintid}.pdf&fileType=pdf"
            )
            import_logic.logger.warning(
                f"used currentHidden/JCOM_003A_0615.pdf for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (fix file name - known case)."
            )

        import_logic.logger.debug(f"{file_url=}")
        response = self.action_manager.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            import_logic.logger.error(
                f"check wjapp login credentials empty pdf galley downloaded: {response.headers['Content-Length']}"
            )
            raise ValueError("Empty pdf galley downloaded")
        return response

    def save_pdf_galley(self, pdf_galley_dj):
        """Save PDF production version in TA.galleys_created"""

        assignment = self.action_manager.article.articleworkflow.get_latest_typesetting_assignment(
            only_completed=False
        )

        # necessary to avoid errors until action "back to typesetter"
        # is implemented, if action TYP_UPLOADS_FOR_PM happens twice like
        # in JCOM_017A_0624
        assignment.galleys_created.clear()

        assert not assignment.galleys_created.exists(), (
            f"We have {assignment.galleys_created.count()} galleys on the TA "
            f"for round {assignment.round.round_number}. Expected none!"
        )

        pdf_galley_file = files.save_file_to_article(
            pdf_galley_dj,
            self.action_manager.article,
            assignment.typesetter,
        )
        pdf_galley = Galley.objects.create(
            file=pdf_galley_file, label="PDF", type="pdf", article=self.action_manager.article, public=False
        )
        assignment.galleys_created.add(pdf_galley)

        assignment.round.article.articleworkflow.save()

        return

    # production version source TARGZ
    def file_source_prod(self):

        # typesetter uploaded a docx replaced with empty tar.gz
        missing_cases = {
            ("JCOM_004C_1020", 3),
        }
        if (self.action_manager.preprintid, self.action_manager.imported_version_num) in missing_cases:
            import_logic.logger.warning(
                f"for {self.action_manager.preprintid}/{self.action_manager.imported_version_num} "
                f"- docx source prod tar.gz known case"
            )
            empty_source_prod_dj = DjangoFile(BytesIO(b""), f"{self.action_manager.preprintid}.tar.gz")
            return empty_source_prod_dj

        (response_source_prod, file_type) = self.action_manager.download_source_prod()
        if response_source_prod.headers["Content-Length"] != "0":
            if file_type == "tar.gz":
                source_prod_dj = import_logic.file_from_response(
                    response_source_prod, f"{self.action_manager.preprintid}.{file_type}"
                )
            elif file_type == "tex":
                source_prod_dj = import_logic.build_targz_archive_from_tex_response(
                    response_source_prod, f"{self.action_manager.preprintid}"
                )
        return source_prod_dj

    def download_source_prod(self):
        """Download tar gz source from production imported version."""

        file_url = (
            f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/"
            f"submission/{self.action_manager.preprintid}.tar.gz&fileType=gz"
        )

        # preprintid of file different from the preprintid of article due to
        # wjapp maintenance change of article type or other type of manual maintenance

        if self.action_manager.preprintid == "JCOM_003C_0622" and self.action_manager.imported_version_num in (2, 3):
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/JCOM_002E_0622.tar.gz&fileType=gz"
            )
            import_logic.logger.warning(
                f"used JCOM_002E_0622.tar.gz for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (change preprint type - known case)."
            )
        if self.action_manager.preprintid == "JCOM_001C_0922" and self.action_manager.imported_version_num == 3:
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/JCOM_001E_0922.tar.gz&fileType=gz"
            )
            import_logic.logger.warning(
                f"used JCOM_001E_0922.tar.gz for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (change preprint type - known case)."
            )
        if self.action_manager.preprintid == "JCOM_004N_0918" and self.action_manager.imported_version_num in (3, 4):
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/JCOM_001C_0918.tar.gz&fileType=gz"
            )
            import_logic.logger.warning(
                f"used JCOM_001C_0918.tar.gz for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (change preprint type - known case)."
            )

        if self.action_manager.preprintid == "JCOM_005A_1116" and self.action_manager.imported_version_num == 3:
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/JCOM_002A_1216.tar.gz&fileType=gz"
            )
            import_logic.logger.warning(
                f"used JCOM_002A_1216.tar.gz for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (change preprint type - known case)."
            )

        if self.action_manager.preprintid == "JCOM_013A_0215" and self.action_manager.imported_version_num == 3:
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/JCOM_1402_2015_A-king-proof.tar.gz&fileType=gz"
            )
            import_logic.logger.warning(
                f"used JCOM_1402_2015_A-king-proof.tar.gz for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (change preprint type - known case)."
            )

        if self.action_manager.preprintid == "JCOM_008A_0215" and self.action_manager.imported_version_num == 4:
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/JCOM_402_2015_A03.tar.gz&fileType=gz"
            )
            import_logic.logger.warning(
                f"used JCOM_402_2015_A03.tar.gz for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (- known case)."
            )

        special_cases = {
            ("JCOMAL_003A_0520", 4): "JCOMAL_001N_0520.tar.gz",
            ("JCOMAL_003A_0520", 5): "JCOMAL_001N_0520.tar.gz",
            ("JCOMAL_001A_1118", 3): "JCOMAL-Orozco.v2.tar.gz",
            ("JCOMAL_001A_1118", 4): "JCOMAL-Orozco.v3.tar.gz",
            ("JCOMAL_002A_1118", 3): "JCOMAL-Marandino_et_al.v2.1.tar.gz",
            ("JCOMAL_002A_1118", 4): "JCOMAL-Marandino_et_al.v3.tar.gz",
            ("JCOMAL_003A_1118", 4): "JCOMAL_003A_1118-proof.tar.gz",
            ("JCOMAL_003A_1118", 5): "JCOMAL-Da_Silva_Lima_Moschem.v3.tar.gz",
            ("JCOMAL_004A_1118", 4): "JCOMAL_003A_1118-proof.tar.gz",
            ("JCOMAL_004A_1118", 5): "JCOMAL-Costa-v3.tar.gz",
            ("JCOMAL_001Y_1118", 2): "JCOMAL_001Y_1118-proof.tar.gz",
            ("JCOMAL_001Y_1118", 3): "JCOMAL-CastilhosAlmeida-v3.tar.gz",
            ("JCOMAL_002Y_1118", 3): "JCOMAL-Cortassa.v2.tar.gz",
            ("JCOMAL_002Y_1118", 4): "JCOMAL-Cortassa.v3.tar.gz",
            ("JCOMAL_001R_1118", 2): "JCOMAL-Poenaru.v2.tar.gz",
            ("JCOMAL_005A_1118", 3): "JCOMAL-NegreteRosenblatt.v2.tar.gz",
            ("JCOMAL_006A_1118", 4): "JCOMAL_006A_1118-proof.tar.gz",
            ("JCOMAL_006A_1118", 5): "JCOMAL-Lima.v3.tar.gz",
            ("JCOMAL_004A_0320", 4): "JCOMAL_001N_0320.tar.gz",
            ("JCOMAL_004A_0320", 5): "JCOMAL_001N_0320.tar.gz",
            ("JCOMAL_001A_1018", 4): "JCOMAL_001A_1018-proof.tar.gz",
            ("JCOMAL_001A_1018", 5): "JCOMAL-Massarani-v4.tar.gz",
        }
        if (self.action_manager.preprintid, self.action_manager.imported_version_num) in special_cases.keys():
            fixed_filename = special_cases[(self.action_manager.preprintid, self.action_manager.imported_version_num)]
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/{fixed_filename}&fileType=gz"
            )
            import_logic.logger.warning(
                f"used {fixed_filename} for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (- known case)."
            )

        file_type = "tar.gz"
        import_logic.logger.debug(f"production source: {file_url=}")
        response = self.action_manager.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            import_logic.logger.warning(
                f"production submission tar.gz empty file: {self.action_manager.article.id} / "
                f"{self.action_manager.preprintid} try tex format"
            )
            file_url = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/"
                f"submission/{self.action_manager.preprintid}.tex&fileType=tex"
            )
            response = self.action_manager.session.get(file_url)
            assert response.status_code == 200, f"Got {response.status_code}!"
            file_type = "tex"
            import_logic.logger.debug(f"production source: {file_url=}")
            if response.headers["Content-Length"] == "0":
                import_logic.logger.error(
                    f"production submission: also tex empty file downloaded: {self.action_manager.article.id} "
                    f"/ {self.action_manager.preprintid}"
                )
                raise ValueError("Tex empty file downloaded")
        return (response, file_type)

    # review files

    def download_source(self):
        """Download docx/doc source for imported version."""

        doc_type = "docx"
        url_first_part = (
            f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/{self.action_manager.preprintid}"
        )
        file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"

        special_cases = {("JCOMAL_001A_1218", 1)}
        if (self.action_manager.preprintid, self.action_manager.imported_version_num) in special_cases:
            url_first_part = (
                f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num}/submission/00-art_div_vf"
            )
            file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"
            import_logic.logger.warning(
                f"used 00-art_div_vf.docx for {self.action_manager.preprintid}/"
                f"{self.action_manager.imported_version_num} (data problem on wjapp- known case)."
            )

        import_logic.logger.debug(f"{file_url=}")
        response = self.action_manager.session.get(file_url)

        if response.status_code == 200:
            if response.headers["Content-Length"] == "0":
                import_logic.logger.debug(
                    f"check wjapp login data empty file {doc_type} downloaded: {response.headers['Content-Length']}"
                )
            else:
                return (response, doc_type)
        else:
            import_logic.logger.warning(f"With docx got {response.status_code}!")

        # try with doc instead of docx
        doc_type = "doc"
        file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"
        import_logic.logger.debug(f"retry with {doc_type} {file_url=}")
        response = self.action_manager.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"

        if response.headers["Content-Length"] == "0":
            import_logic.logger.error(
                f"check wjapp login credentials empty file {doc_type} downloaded: {response.headers['Content-Length']}"
            )
            raise ValueError("Empty doc/docx file downloaded")
        return (response, doc_type)

    def regenerate_production_archives(self):
        """Necessary to regenerate the production archives."""

        # Before to download the production zip it could be necessary to regenerate it.
        # It is done doing a request to the preprint wjapp "All versions" page
        # without to read the response, e.g. url:
        # https://jcom.sissa.it/jcom/admin/docPage.jsp?docPgType=versions&docId=JCOM_001A_0823
        #
        # To read the response is not important, but wjapp server seems randomly
        # to "chunck" it, therefore has been except ChunkedEncodingError and only logged it as ERROR.
        # This exception does not block the regeneration of the production archives, which is the
        # reason of the request.

        # i.e. JCOM_014A_0524: broken wjapp version page repaired on wjapp side.
        # This problem is independent by the management of the exception ChunkedEncodingError

        file_url_versions_page = (
            f"{self.action_manager.session.login_base_url}/admin/docPage.jsp?"
            f"docPgType=versions&docId={self.action_manager.preprintid}"
        )
        try:
            response = self.action_manager.session.get(file_url_versions_page)
            assert response.status_code == 200, f"Got {response.status_code}!"
        except requests.exceptions.ChunkedEncodingError:
            # known cases: JCOM_003A_0724 JCOM_004N_1024 JCOM_008A_0125 JCOM_014A_0524
            import_logic.logger.error(f"Exception: ChunkedEncodingError {file_url_versions_page}")
            raise RuntimeError("ChunkedEncodingError ")

    def download_source_compressed_archive(self):
        """Download compressed zip with submission folder for imported version."""

        doc_type = "zip"
        url_first_part = (
            f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/production/{self.action_manager.preprintid}"
        )
        file_url = f"{url_first_part}.{doc_type}&fileType={doc_type}"
        import_logic.logger.debug(f"{file_url=}")
        response = self.action_manager.session.get(file_url)

        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            import_logic.logger.warning(
                f"check data empty file {doc_type} downloaded: {response.headers['Content-Length']} try regeneration "
            )
            # the regeneration of the zip archive could be necessary also after the
            # first wjapp version, if for example the zip file is missing only in version 2
            # if the zip files have already been regenerated this if branch is not executed
            # in the next versions
            self.action_manager.regenerate_production_archives()
            import_logic.logger.debug(
                f"production archives regenerated during import of version {self.action_manager.imported_version_num}"
            )
            response = self.action_manager.session.get(file_url)
            if response.headers["Content-Length"] == "0":
                import_logic.logger.error(
                    f"check wjapp login data empty file {doc_type}downloaded: {response.headers['Content-Length']}"
                )
                raise ValueError("Empty zip downloaded")
        return (response, doc_type)

    def save_manuscript(self, manuscript_dj):
        """Save PDF manuscript"""

        # remove previous files relations already saved as historical files
        # the manuscript has only the current files
        self.action_manager.article.manuscript_files.clear()

        manuscript_file = files.save_file_to_article(
            manuscript_dj,
            self.action_manager.article,
            self.action_manager.article.correspondence_author,
        )
        self.action_manager.article.manuscript_files.add(manuscript_file)
        manuscript_file.label = "PDF"
        manuscript_file.description = ""
        manuscript_file.save()
        self.action_manager.article.save()

        return

    def save_source(self, source_dj, doc_type):
        """Save docx/doc as source file"""

        # there is more than one source file for version, therefore
        # they are not clear()

        source_file = files.save_file_to_article(
            source_dj,
            self.action_manager.article,
            self.action_manager.article.correspondence_author,
        )
        self.action_manager.article.source_files.add(source_file)
        source_file.label = doc_type.upper()
        source_file.description = ""
        source_file.save()
        self.action_manager.article.save()

        return

    # wjapp attachments - data figurs files
    def download_wjapp_attachment(self, dff_data):
        """Download one wjapp attachment for imported version."""

        # url ex: JCOM_001A_0524/2/attachments/JCOM_001A_0524_ATTACH00360924.docx&fileType=Attachment

        url_end_part = f"{dff_data['attachID']}.{dff_data['attachFormat']}&fileType={dff_data['attachType']}"
        file_url = (
            f"{self.action_manager.url_base}{self.action_manager.preprintid}/"
            f"{self.action_manager.imported_version_num}/attachments/{url_end_part}"
        )
        import_logic.logger.debug(f"{file_url=}")
        response = self.action_manager.session.get(file_url)
        assert response.status_code == 200, f"Got {response.status_code}!"
        if response.headers["Content-Length"] == "0":
            import_logic.logger.error(
                f"check wjapp login credentials empty DFF downloaded: {response.headers['Content-Length']}"
            )
            raise ValueError("Empty DFF downloaded")
        return response

    def save_data_figure_file(self, dff_dj, dff_data):
        """Save wjapp attachment as data figure file"""

        dff_file = files.save_file_to_article(
            dff_dj,
            self.action_manager.article,
            self.action_manager.article.correspondence_author,
        )
        self.action_manager.article.data_figure_files.add(dff_file)
        dff_file.label = dff_data["attachType"]
        dff_file.description = f"{dff_data['attachTitle']} {dff_data['attachDescription']}"
        dff_file.save()
        self.action_manager.article.save()

        return
