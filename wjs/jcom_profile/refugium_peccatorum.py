"""Copy of some code from wjs_review plugin.

This code is used only by the import_from_wjapp command and shuld be dropped when go live.
Original code is from wjs_plugin.logic__production

See also specs#883
"""

import dataclasses
import os
import shutil
import tarfile
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import lxml.html
import magic
import requests
from core.models import File as JanewayFile
from core.models import Galley
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.http import HttpRequest
from lxml.html import HtmlElement
from production.logic import save_galley, save_galley_image
from submission.models import Article
from utils.logger import get_logger

from wjs.jcom_profile.import_utils import (
    decide_galley_label,
    evince_language_from_filename_and_article,
    process_body,
)

logger = get_logger(__name__)
Account = get_user_model()


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
        """Unpack an archive received from jcomassistant.

        Accept the archive in the form of a bytes string.

        Create and use a temporary folder.
        The caller should clean up if necessary.
        """
        unpack_dir = tempfile.mkdtemp()

        # Not sure if we receive zip or tar.gz
        # (see also yakunin.archive.Archive.epatografo)
        mime = magic.Magic(mime=True)
        mime_type = mime.from_buffer(self.archive_with_galleys)

        # Use BytesIO to treat bytes data as a file
        with BytesIO(self.archive_with_galleys) as file_obj:
            if mime_type == "application/x-compressed-tar":
                with tarfile.open(fileobj=file_obj, mode="r:gz") as tar:
                    tar.extractall(path=unpack_dir)
            else:
                with zipfile.ZipFile(file_obj, "r") as zip_ref:
                    zip_ref.extractall(unpack_dir)

        unpack_dir = Path(unpack_dir)

        logger.debug(f"...jcomassistant processed files are in {unpack_dir}.")
        self.path = unpack_dir
        return self.path

    def reemit_info_and_up(self) -> bool:
        """Emit as log messages lines read from the given log file.

        Expect the logfile to contain log-formatted lines suchs as:
        DEBUG From: ...

        Re-emit only info, wraning, error and critical.

        Also return False if any error or critical was found.
        """
        has_error_or_critical = False

        # log files are called something like
        # - galleys_en-xxx.epub_log
        # - galleys_en-xxx.html_log
        # - galleys_en-xxx.srvc_log
        # We are going to use only the service log (*.srvc_log)
        srvc_log_files = list(self.path.glob("galley*.srvc_log"))
        if len(srvc_log_files) != 1:
            logger.warning(f"Found {len(srvc_log_files)} srvc_log files. Re-emitting both.")
        for srvc_log_file in srvc_log_files:
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
        if not self.reemit_info_and_up():
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
            # Do we still need this? It appear to drop a possible query-string...
            img_src = image.attrib["src"].split("?")[0]
            # Images are always in a dir, so we get the last two parts of the path
            # and rebuild the img path relative to our working dir
            img_src = self.path / os.path.join(*img_src.split("/")[-2:])
            if not img_src.exists():
                logger.error(f"{img_src} does not exists. {image.attrib['src']=}")
                continue
            img_obj: JanewayFile = self.download_and_store_article_file(img_src)
            # TBV: the `src` attribute is relative to the article's URL
            image.attrib["src"] = img_obj.label

        with open(galley_file.self_article_path(), "wb") as out_file:
            out_file.write(lxml.html.tostring(html, pretty_print=False))

    def save_html(self, filename: str = None, label: str = None, language: str = None):
        """Set the given html file (or the only one) as HTML galley.

        Process it to adapt to our web page (drop how-to-cite, etc.)
        and deal with possible images.
        """
        if filename is None:
            filename = [f for f in self.path.iterdir() if f.suffix == ".html"][0]
        else:
            filename = Path(filename)

        filename = self.path / filename
        html_galley_text = open(filename).read()

        if label is None or language is None:
            language = evince_language_from_filename_and_article(str(filename), self.article)
            label = "HTML"

        processed_html_galley_as_bytes = process_body(html_galley_text, style="wjapp", lang=language)
        html_galley_file = File(BytesIO(processed_html_galley_as_bytes), filename.name)
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

    def save_epub(self, filename: str = None, label: str = None, language: str = None):
        """Set the first epub file as EPUB galley."""
        if filename is None:
            filename = [f for f in self.path.iterdir() if f.suffix == ".epub"][0]
        else:
            filename = Path(filename)

        filename = self.path / filename
        epub_galley_file = File(open(filename, "rb"), name=filename.name)
        file_mimetype = "application/epub+zip"
        if label is None or language is None:
            label, language = decide_galley_label(file_name=str(filename), file_mimetype=file_mimetype)
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

    def save_pdf(self, filename: str = None, label: str = None, language: str = None):
        """Set the given pdf file (or the only one) as PDF galley."""
        if filename is None:
            pdf_files = [f for f in self.path.iterdir() if f.suffix == ".pdf"]
            if len(pdf_files) != 1:
                # TODO: temporary workaround! In production, this should trigger a stopping error
                logger.error(f"Cannot find PDF in galleys of {self.article.id}")
                return
            filename = pdf_files[0]
        else:
            filename = Path(filename)

        filename = self.path / filename
        pdf_galley_file = File(open(filename, "rb"), name=filename.name)
        file_mimetype = "application/pdf+zip"
        if label is None or language is None:
            label, language = decide_galley_label(file_name=str(filename), file_mimetype=file_mimetype)
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
        self.path = self.unpack_targz_from_jcomassistant()
        green_light, reason = self._check_conditions()
        if not green_light:
            logger.error(f"Galleys generation failed for {self.article.id}: {reason}")
        else:
            galleys_created = [self.save_epub(), self.save_html(), self.save_pdf()]
        shutil.rmtree(self.path)
        return galleys_created


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
        return response
