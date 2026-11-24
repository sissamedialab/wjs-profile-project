"""Build the galleys of the translation of a paper (see specs#990).

jcomassistant builds the galleys of one TeX file per request (the "main" one named in ``spawn.ini``), so the
translation that the typesetter adds to the sources is never compiled. Here we build its galleys on request, with
the same publication data as the main galleys, and hand jcomassistant's answer back to the caller: nothing is
saved, neither in the DB nor in the filesystem. The galleys are then uploaded by hand from the article manager.

The calls to jcomassistant are copies of the ones in ``logic__production.py`` and ``synctex/logic.py`` (see
specs#3400).
"""

import configparser
import dataclasses
import io
import json
import re
import zipfile
from collections import Counter
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone
from submission.models import STAGE_PUBLISHED, Article
from utils.logger import get_logger

from ..models import ArticleWorkflow
from ..utils import guess_typesetted_texfile_name
from .logic import INVALID_REQUEST_CODE

logger = get_logger(__name__)

#: `ValidationError` code for a failure caused by the data stored for the paper (missing sources, DOI, ...):
#: the API answers it with a "409". Failures without a code are jcomassistant's ("502"), and the ones with
#: `INVALID_REQUEST_CODE` are about the archive ("400").
CONFLICT_CODE = "conflict"

SPAWN_INI = "spawn.ini"

#: Keys of the publication data that jcomassistant injects in the TeX source (see jcomassistant's ``PubData``).
PUBLICATION_DATA_KEYS = ("date_published", "year", "volume", "issue", "eid", "counter", "section", "doi")

#: Keys of the publication data that must agree with the ones computed from the DB. The counter is not among them:
#: once the paper is published, ``collect_publication_data()`` counts the paper itself too.
STABLE_PUBLICATION_DATA_KEYS = ("date_published", "year", "volume", "issue", "eid", "section")

#: The DOI that the TeX templates carry until the publication data are injected.
PLACEHOLDER_DOI_RE = re.compile(r"/0\.0+$")

#: For each galley format, the files that jcomassistant would take from the archive instead of producing them
#: (see jcomassistant's ``Spawn.is_produced()``): the galley itself and the intermediate files it is built from.
#: Extensions are relative to the stem of the main TeX file.
PRECOMPILED_EXTENSIONS = {
    "pdf": (".pdf",),
    "epub": (".epub", ".html", ".xhtml", ".opf", ".json"),
}

LANGUAGE_RE = re.compile(r"\\publicationLanguage\{([A-Za-z]{2,3})\}", re.IGNORECASE)


def _invalid(message: str) -> ValidationError:
    return ValidationError(message, code=INVALID_REQUEST_CODE)


def _conflict(message: str) -> ValidationError:
    return ValidationError(message, code=CONFLICT_CODE)


def find_translation(names: list[str], spawn_ini: str | None, main_tex_name: str) -> tuple[str, str]:
    """
    Find the main TeX file and the translation among the names of the files of an archive.

    The archive must contain exactly two TeX files at its root, and a ``spawn.ini`` naming one of them as the
    main file (``[tex] main=...``). By convention, the main file is named after the paper (`main_tex_name`, e.g.
    ``JCOM_1234.tex``), because that is the file that WJS injects the publication data into.

    :param names: the names of the files in the archive.
    :param spawn_ini: the content of the archive's ``spawn.ini``, or None if the archive has none.
    :param main_tex_name: the expected name of the main TeX file.
    :return: the names of the main TeX file and of the translation.
    :raises ValidationError: (`INVALID_REQUEST_CODE`) if any of the rules above is not met.
    """
    if spawn_ini is None:
        raise _invalid(f"The archive has no {SPAWN_INI}: cannot tell the main TeX file from the translation.")
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read_string(spawn_ini)
    except configparser.Error as exception:
        raise _invalid(f"Cannot read {SPAWN_INI}: {exception}")
    main = config.get("tex", "main", fallback="").strip()
    if not main:
        raise _invalid(f"{SPAWN_INI} does not name the main TeX file (expected [tex] main={main_tex_name}).")

    tex_names = sorted(name for name in names if "/" not in name and name.endswith(".tex"))
    if len(tex_names) != 2:
        raise _invalid(
            f"The archive must contain exactly two TeX files at its root (the main one and the translation),"
            f" found {len(tex_names)}: {', '.join(tex_names) or 'none'}."
        )
    if main not in tex_names:
        raise _invalid(f"{SPAWN_INI} names {main} as main TeX file, but the archive does not contain it.")
    if main != main_tex_name:
        raise _invalid(
            f"{SPAWN_INI} names {main} as main TeX file, but the main TeX file must be {main_tex_name}"
            f" (and the translation e.g. {Path(main_tex_name).stem}_eng.tex)."
        )
    translation = next(name for name in tex_names if name != main)
    return main, translation


def find_duplicated_galley_types(article: Article) -> list[str]:
    """
    Return the extensions shared by more than one galley of the article (e.g. ``["epub", "pdf"]``).

    More galleys of the same type mean that the paper has translation galleys (e.g. ``PDF`` and ``PDF (en)``).
    """
    extensions = Counter(
        PurePosixPath(galley.file.original_filename).suffix.lower().lstrip(".")
        for galley in article.galley_set.select_related("file")
        if galley.file
    )
    return sorted(extension for extension, count in extensions.items() if count > 1)


@dataclasses.dataclass
class TranslationGalleys:
    """What jcomassistant built for the translation."""

    content: bytes
    "jcomassistant's answer: a zip with the galleys, their logs and the service log."

    tex_name: str
    "The name of the translation's TeX file."

    language: str
    "The language of the translation, as found in the TeX file (e.g. ``eng``)."

    not_compiled: list[str]
    "The galley formats that jcomassistant took from the archive instead of compiling them."

    @property
    def file_name(self) -> str:
        """Return the name under which the answer is served."""
        return f"{Path(self.tex_name).stem}_galleys.zip"


@dataclasses.dataclass
class BuildTranslationGalleys:
    """
    Build the galleys of the translation of a paper.

    - take the paper's sources: the "Final sources" if the paper is published, otherwise the files of the latest
      typesetting assignment;
    - find the translation (the TeX file that is not the main one);
    - inject the publication data into it: the same as the main TeX file if the paper is published, otherwise
      a simulation (as if the paper were published now);
    - ask jcomassistant to build its galleys from a copy of the sources where the translation is the main file.

    Nothing is saved, neither in the DB nor in the filesystem.
    """

    workflow: ArticleWorkflow
    formats: list[str]
    "The galley formats to build (e.g. pdf, epub)."

    @property
    def article(self) -> Article:
        """Return the paper."""
        return self.workflow.article

    @property
    def is_published(self) -> bool:
        """Tell if the paper has been published (the "Final sources" exist only then)."""
        return self.article.stage == STAGE_PUBLISHED

    def get_sources(self) -> bytes:
        """
        Return the content of the archive with the paper's sources.

        :raises ValidationError: (`CONFLICT_CODE`) if the paper has no sources.
        """
        if self.is_published:
            source_file = self.workflow.publication_galleys_source_file
            if source_file is None:
                raise _conflict(f"Paper {self.article.pk} is published but has no final sources.")
        else:
            assignment = self.workflow.get_latest_typesetting_assignment(only_completed=False)
            if assignment is None:
                raise _conflict(f"Paper {self.article.pk} has no typesetting assignment.")
            try:
                source_file = assignment.files_to_typeset.get()
            except ObjectDoesNotExist:
                raise _conflict(f"The latest typesetting assignment of paper {self.article.pk} has no files.")
        path = Path(source_file.self_article_path())
        if not path.exists():
            raise _conflict(f"The sources of paper {self.article.pk} are missing from the filesystem.")
        return path.read_bytes()

    def get_publication_data(self, main_tex: bytes, main_tex_name: str) -> dict[str, str]:
        """
        Return the publication data to inject into the translation.

        :raises ValidationError: see `read_publication_data` and `simulate_publication_data`.
        """
        if self.is_published:
            return self.read_publication_data(main_tex, main_tex_name)
        return self.simulate_publication_data()

    def read_publication_data(self, main_tex: bytes, main_tex_name: str) -> dict[str, str]:
        """
        Return the publication data that have been injected in the main TeX file at publication.

        They are read back from the TeX (via jcomassistant), not computed anew from the DB: once the paper is
        published, ``collect_publication_data()`` counts the paper itself among the published papers of the
        issue, and the counter would be wrong.

        :raises ValidationError: if jcomassistant fails or does not return the whole publication data (no code),
            or (`CONFLICT_CODE`) if the main file has never been injected or if its data disagree with the DB.
        """
        url = f"{settings.JCOMASSISTANT_URL}texdata"
        files = {"file": (main_tex_name, main_tex, "text/plain")}
        try:
            response = requests.post(url=url, files=files, timeout=10)
        except requests.RequestException as exception:
            raise ValidationError(f"Cannot reach jcomassistant at {url}: {exception}")
        # A partial answer (206) is fine, as long as the publication data are complete (checked below)
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
            raise ValidationError(f"Unexpected status code {response.status_code} from jcomassistant ({url}).")
        try:
            publication_data = response.json()["publication_data"]
        except (ValueError, KeyError, TypeError):
            raise ValidationError(f"jcomassistant did not return the publication data of {main_tex_name}.")
        if missing := [key for key in PUBLICATION_DATA_KEYS if not publication_data.get(key)]:
            raise ValidationError(
                f"jcomassistant did not return the whole publication data of {main_tex_name}"
                f" (missing: {', '.join(missing)})."
            )
        data = {key: str(publication_data[key]) for key in PUBLICATION_DATA_KEYS}

        if "?" in data["date_published"] or PLACEHOLDER_DOI_RE.search(data["doi"]):
            raise _conflict(
                f"{main_tex_name} in the final sources of paper {self.article.pk} has no publication data"
                f" (published: {data['date_published']}, DOI: {data['doi']})."
            )
        self._check_against_db(data, main_tex_name)
        return data

    def _check_against_db(self, data: dict[str, str], main_tex_name: str):
        """
        Ensure that the publication data read from the TeX agree with the DB.

        If they don't, something has been changed after publication and someone must decide which is right.

        :raises ValidationError: (`CONFLICT_CODE`) if they disagree, or if they cannot be computed from the DB.
        """
        try:
            expected = self.workflow.collect_publication_data()
        except (ValueError, NotImplementedError, AttributeError, AssertionError) as exception:
            raise _conflict(f"Cannot compute the publication data of paper {self.article.pk}: {exception}")
        expected["doi"] = self.article.get_doi() or ""
        differences = [
            f"{key}: {data[key]} (TeX) ≠ {expected[key]} (DB)"
            for key in (*STABLE_PUBLICATION_DATA_KEYS, "doi")
            if str(expected[key]) != data[key]
        ]
        if differences:
            raise _conflict(
                f"The publication data in {main_tex_name} disagree with the ones of paper {self.article.pk}: "
                + "; ".join(differences)
            )

    def simulate_publication_data(self) -> dict[str, str]:
        """
        Return the publication data that the paper would get if it were published now.

        Nothing is saved: as in ``SimulatePublicationIdentifiers``, the computation runs on a fresh copy of the
        workflow, inside a transaction that is always rolled back.

        :raises ValidationError: (`CONFLICT_CODE`) if the paper has no issue, no DOI, or if the data cannot be
            computed.
        """
        with transaction.atomic():
            workflow = ArticleWorkflow.objects.select_related("article").get(pk=self.workflow.pk)
            article = workflow.article
            if not article.primary_issue:
                raise _conflict(f"Paper {article.pk} is not associated to any issue.")
            doi = article.get_doi()
            if not doi:
                raise _conflict(f"Paper {article.pk} has no DOI.")
            if not article.date_published:
                article.date_published = timezone.now()
            try:
                data = workflow.collect_publication_data()
            except (ValueError, NotImplementedError, AttributeError, AssertionError) as exception:
                raise _conflict(f"Cannot compute the publication data of paper {article.pk}: {exception}")
            # Belt and braces: whatever happened above, nothing must reach the DB
            transaction.set_rollback(True)
        data["doi"] = doi
        return {key: str(data[key]) for key in PUBLICATION_DATA_KEYS}

    def inject(self, tex_name: str, tex: bytes, publication_data: dict[str, str]) -> bytes:
        """
        Inject the publication data into the given TeX file (via jcomassistant) and return it.

        The payload is the same as ``BeginPublication._prepare_source()``'s.

        :raises ValidationError: if jcomassistant fails.
        """
        url = f"{settings.JCOMASSISTANT_URL}pubdata"
        files = {"file": (tex_name, tex, "text/plain")}
        data = {"data": json.dumps({"publication_data": publication_data})}
        try:
            response = requests.post(url=url, files=files, data=data, timeout=15)
        except requests.RequestException as exception:
            raise ValidationError(f"Cannot reach jcomassistant at {url}: {exception}")
        if response.status_code != HTTPStatus.OK:
            raise ValidationError(f"Unexpected status code {response.status_code} from jcomassistant ({url}).")
        return response.content

    @staticmethod
    def build_archive(archive: zipfile.ZipFile, translation: str, injected_tex: bytes, spawn_ini: str) -> bytes:
        """
        Return a copy of the archive where the (injected) translation is the main TeX file.

        All the other files are copied as they are; ``spawn.ini`` keeps all its settings, but names the
        translation as main file.
        """
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(spawn_ini)
        config.set("tex", "main", translation)
        new_spawn_ini = io.StringIO()
        config.write(new_spawn_ini)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as new_archive:
            for item in archive.infolist():
                if item.filename in (SPAWN_INI, translation):
                    continue
                new_archive.writestr(item, archive.read(item.filename))
            new_archive.writestr(translation, injected_tex)
            new_archive.writestr(SPAWN_INI, new_spawn_ini.getvalue())
        return buffer.getvalue()

    def find_not_compiled(self, names: list[str], translation: str) -> list[str]:
        """
        Return the requested formats that jcomassistant would take from the archive instead of compiling them.

        jcomassistant treats an output file already in the archive as produced. This is a sort of safety net (the
        typesetter can compile the translation by hand), but such galleys don't have the injected publication data,
        so the caller must know.
        """
        stem = Path(translation).stem
        root_names = {name for name in names if "/" not in name}
        not_compiled = []
        for fmt in self.formats:
            candidates = [f"{stem}{extension}" for extension in PRECOMPILED_EXTENSIONS.get(fmt, ())]
            if found := [name for name in candidates if name in root_names]:
                logger.warning(
                    f"Translation galleys of paper {self.article.pk}: {', '.join(found)} found in the archive."
                    f" The {fmt.upper()} is taken from the archive, not compiled: publication data not injected."
                )
                not_compiled.append(fmt)
        return not_compiled

    def request_galleys(self, archive: bytes, archive_name: str) -> bytes:
        """
        Ask jcomassistant to build the galleys from the given archive and return its answer.

        As ``JcomAssistantClient``, without timeout.

        :raises ValidationError: if jcomassistant fails.
        """
        url = f"{settings.JCOMASSISTANT_URL}galleys?{urlencode([('generate', fmt) for fmt in self.formats])}"
        files = {"file": (archive_name, archive, "application/zip")}
        try:
            response = requests.post(url=url, files=files)  # noqa: S113 (as JcomAssistantClient)
        except requests.RequestException as exception:
            raise ValidationError(f"Cannot reach jcomassistant at {url}: {exception}")
        if response.status_code != HTTPStatus.OK:
            raise ValidationError(f"Unexpected status code {response.status_code} from jcomassistant ({url}).")
        return response.content

    @staticmethod
    def find_language(translation: str, tex: bytes, main_tex_name: str) -> str:
        """Return the language of the translation, from its ``\\publicationLanguage`` or its name."""
        if match := LANGUAGE_RE.search(tex.decode("utf-8", errors="replace")):
            return match.group(1).lower()
        return Path(translation).stem.removeprefix(f"{Path(main_tex_name).stem}_") or "unknown"

    def run(self) -> TranslationGalleys:
        """
        Build the galleys of the translation.

        :raises ValidationError: with code `INVALID_REQUEST_CODE` if the archive does not follow the rules (see
            `find_translation`), `CONFLICT_CODE` if the stored data do not allow to build the galleys, no code if
            jcomassistant fails.
        """
        sources = self.get_sources()
        main_tex_name = guess_typesetted_texfile_name(self.article)
        try:
            archive = zipfile.ZipFile(io.BytesIO(sources))
        except zipfile.BadZipFile as exception:
            raise _invalid(f"The sources of paper {self.article.pk} are not a zip archive: {exception}")
        with archive:
            names = archive.namelist()
            spawn_ini = archive.read(SPAWN_INI).decode("utf-8") if SPAWN_INI in names else None
            main, translation = find_translation(names, spawn_ini, main_tex_name)
            translation_tex = archive.read(translation)

            publication_data = self.get_publication_data(archive.read(main), main)
            injected_tex = self.inject(translation, translation_tex, publication_data)
            not_compiled = self.find_not_compiled(names, translation)
            new_archive = self.build_archive(archive, translation, injected_tex, spawn_ini)

        language = self.find_language(translation, translation_tex, main_tex_name)
        logger.info(
            f"Building the galleys of {translation} ({language}) for paper {self.article.pk}"
            f" ({', '.join(self.formats)})."
        )
        content = self.request_galleys(new_archive, f"{Path(main_tex_name).stem}.zip")
        return TranslationGalleys(content=content, tex_name=translation, language=language, not_compiled=not_compiled)
