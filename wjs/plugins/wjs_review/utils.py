import os
import re
import tempfile
import zipfile
from typing import IO

from django.db.models import QuerySet
from submission.models import Article

from .models import WorkflowReviewAssignment


def get_other_review_assignments_for_this_round(
    review_assignment: WorkflowReviewAssignment,
) -> QuerySet[WorkflowReviewAssignment]:
    """Return a queryset of ReviewAssigments for the same article/round of the given review_assigment.

    The queryset does not include the give review_assigment.

    This is useful because after actions such as accept/decline review assignment or submit review or others we decide
    whether to create/delete some editor reminder based on the presence/state of other review assignments on the
    article.

    """
    # Janeway's article.active_reviews and similar do _not_ consider the review round, and, even if the business
    # logic should prevent any issue concerning reminders (i.e. when a new round is created, all reminders are
    # dealt with), we should look only at the review assignments of the current round.

    # Not using `article.current_review_round_object()` should hit the db once less.
    review_round = review_assignment.review_round
    my_id = review_assignment.pk
    other_assignments_for_this_round = (
        WorkflowReviewAssignment.objects.filter(
            article=review_assignment.article,
            editor=review_assignment.editor,
            review_round=review_round,
        )
        .exclude(id=my_id)
        .exclude(decision="withdrawn")
    )
    return other_assignments_for_this_round


def get_tex_source_file_from_archive(source_files_archive, tex_source_name: str) -> IO:
    """Extract the source file of the article galleys.

    Return the main TeX file, the one that contains the LaTeX preamble.
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
    return os.path.join(tempdir, tex_source_name)


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
