import re
import tempfile
import urllib
import urllib.request
import xml.etree.ElementTree as ET  # noqa
import zipfile
from typing import IO

import requests
from django.conf import settings
from django.utils.module_loading import import_string
from review.models import ReviewRound
from submission.models import Article

from .models import WorkflowReviewAssignment, WorkflowReviewAssignmentQuerySet


def get_not_withdrawn_review_assignments_for_this_round(
    article: Article,
    review_round: ReviewRound,
) -> WorkflowReviewAssignmentQuerySet:
    """Return a queryset of ReviewAssigments for a given article / review round.

    The queryset does not include the give review_assigment.

    """
    # Janeway's article.active_reviews and similar do _not_ consider the review round, and, even if the business
    # logic should prevent any issue concerning reminders (i.e. when a new round is created, all reminders are
    # dealt with), we should look only at the review assignments of the current round.

    # Not using `article.current_review_round_object()` should hit the db once less.
    assignments_for_this_round = WorkflowReviewAssignment.objects.filter(
        article=article,
        review_round=review_round,
    ).not_withdrawn()
    return assignments_for_this_round


def get_other_review_assignments_for_this_round(
    review_assignment: WorkflowReviewAssignment,
) -> WorkflowReviewAssignmentQuerySet:
    """Return a queryset of ReviewAssigments for the same article/round of the given review_assigment.

    The queryset does not include the give review_assigment.

    This is useful because after actions such as accept/decline review assignment or submit review or others we decide
    whether to create/delete some editor reminder based on the presence/state of other review assignments on the
    article.

    Internally it used :py:func:`get_not_withdrawn_review_assignments_for_this_round`.
    """
    return get_not_withdrawn_review_assignments_for_this_round(
        review_assignment.article, review_assignment.review_round
    ).exclude(
        pk=review_assignment.pk,
    )


def get_tex_source_file_path_from_archive(source_files_archive, tex_source_name: str) -> str:
    """Extract the source file of the article galleys.

    Return the tmp folder containing the main TeX file, the one that contains the LaTeX preamble.
    """
    # TODO: talk with Elia on the opportunity of buildind a "texfile utils" library with similar functions
    tempdir = tempfile.mkdtemp()
    with zipfile.ZipFile(source_files_archive) as zip_file:
        if tex_source_name in zip_file.namelist():
            zip_file.extract(tex_source_name, tempdir)
        else:
            raise FileNotFoundError(
                f"{tex_source_name} not found in the archive {source_files_archive}",
            )
    return tempdir


def guess_typesetted_texfile_name(article: Article) -> str:
    tex_source_name = f"{article.journal.code}_{article.id}.tex"
    return tex_source_name


def tex_file_has_queries(tex_file: IO) -> bool:
    """Check if the TeX file contains queries."""
    with open(tex_file, encoding="utf-8") as source:
        if re.search(r"^\s*\\proofs\W", source.read(), re.MULTILINE):
            return True
        else:
            return False


def get_report_form(journal_code: str):
    form_path = settings.WJS_REVIEW_CUSTOM_REPORT_FORMS.get(
        journal_code,
        settings.WJS_REVIEW_CUSTOM_REPORT_FORMS.get(None),
    )
    return import_string(form_path)


def fetch_arxiv_metadata(arxiv_id: str) -> dict:
    """
    Query arXiv and retrieve metadata, PDF and source files.

    Save the files in the cwd.

    Return:
       the metadata as a dictionary.
       Eventual errors are in the "errors" key.

    """
    errors = {}
    result = {
        "title": None,
        "abstract": None,
        "category_term": None,
        "source_file": None,
        "pdf_file": None,
        "errors": errors,
    }

    try:
        url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
        response = urllib.request.urlopen(url)
        xml_content = response.read().decode("utf-8")
        root = ET.fromstring(xml_content)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        entry = root.find("atom:entry", ns)

        title_elem = entry.find("atom:title", ns)
        abstract_elem = entry.find("atom:summary", ns)
        category_elem = entry.find("arxiv:primary_category", ns)

        result["title"] = title_elem.text
        result["abstract"] = abstract_elem.text
        result["category_term"] = category_elem.attrib.get("term")

    except AttributeError as e:
        errors["query"] = f"Attribute error: {e}"
        return result
    except urllib.error.URLError as e:
        errors["query"] = f"URL error: {e.reason}"
        return result
    except ET.ParseError as e:
        errors["query"] = f"XML parse error: {str(e)}"
        return result
    except Exception as e:
        errors["query"] = str(e)
        return result

    specs = [
        ("source_file", "https://arxiv.org/src/{}", ".tar.gz"),
        ("pdf_file", "https://arxiv.org/pdf/{}", ".pdf"),
    ]

    for file_name, base_url, suffix in specs:
        url = base_url.format(arxiv_id)
        dest = f"{file_name}-{arxiv_id}{suffix}"
        try:
            resp = requests.get(url)
            if resp.status_code == 200:
                with open(dest, "wb") as f:
                    f.write(resp.content)
                result[file_name] = dest
            else:
                errors[file_name] = f"HTTP {resp.status_code}"
        except Exception as e:
            errors[file_name] = str(e)

    return result
