"""Logic for the synchronization of article metadata between TeX sources and DB."""

import dataclasses
import zipfile
from io import BytesIO

import requests
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from typesetting.models import TypesettingAssignment

from ..models import ArticleWorkflow


@dataclasses.dataclass
class MetadataFromTeX:
    """
    Extract metadata from TeX.

    Send the latest typesetted tex source file to jcomassistant and
    receive the metadata extracted from that.
    """

    workflow: ArticleWorkflow

    # TODO: allow for type-hint TexData without depending from jcomassistant
    def get_raw_data(self) -> dict:
        """
        Ask Jcomassistant to extract structured metadata from TeX.

        Raise:
          HTTPError: if jcomassistant returned something different that 200

        """
        # Use the source file from the latest typesetting round.  We probably will never change these data after
        # publication (when the source file is AW.publication_galleys_source_file), but even if we do, title and
        # abstract should be the same in the two source files.
        tex_source_file = self._get_source_file()
        files = {"file": tex_source_file}
        url = f"{settings.JCOMASSISTANT_URL}texdata"
        response = requests.post(url=url, files=files, timeout=10)
        if response.status_code != 200:
            msg = f"Unexpected status code {response.status_code} for {url}"
            raise requests.exceptions.HTTPError(msg)
        return response.json()

    # TODO: refactor with logic__production.BeginPublication._get_source_file()
    def _get_source_file(self) -> BytesIO:
        """
        Extract the source file of the article galleys.

        Use the latest available files:
        if the latest production version (typesetting-assignment) has just started,
        the files are taken from the previous version.

        Return the main TeX file.

        Raise:
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
