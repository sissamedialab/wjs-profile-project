import re
import tempfile
import xml.etree.ElementTree as ET  # noqa
import zipfile
from typing import IO

from core import models as core_models
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
    """Check if the TeX file contains queries, can has proofs or queries not removed."""
    with open(tex_file, encoding="utf-8") as source:
        tex = source.read()
        if re.search(r"^\s*\\proofs\W", tex, re.MULTILINE):
            return True
        if re.search(r"^\s*\\queryOptions\{(?!.*remove).*\}", tex, re.MULTILINE):
            return True
        return False


def get_report_form(journal_code: str):
    form_path = settings.WJS_REVIEW_CUSTOM_REPORT_FORMS.get(
        journal_code,
        settings.WJS_REVIEW_CUSTOM_REPORT_FORMS.get(None),
    )
    return import_string(form_path)


def remove_existing_files_from_filesystem(article_id: int, filename: str):
    """Delete from the filesystem all the files with a given name and relate to a given article."""
    files = core_models.File.objects.filter(article_id=article_id, original_filename=filename)
    for file in files:
        file.delete()
